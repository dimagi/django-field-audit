from itertools import islice

from django.db import models

from .const import BOOTSTRAP_BATCH_SIZE


class AuditService:
    """Service class containing the core audit logic extracted from AuditEvent.

    This class can be subclassed to provide custom audit implementations while
    maintaining backward compatibility with the existing AuditEvent API.
    """

    ATTACH_FIELD_NAMES_AT = "__field_audit_field_names"
    ATTACH_INIT_VALUES_AT = "__field_audit_init_values"
    ATTACH_INIT_M2M_VALUES_AT = "__field_audit_init_m2m_values"

    def attach_field_names(self, model_class, field_names):
        """Attaches a collection of field names to a Model class for auditing.

        :param model_class: a Django Model class under audit
        :param field_names: collection of field names to audit on the model
        """
        setattr(model_class, self.ATTACH_FIELD_NAMES_AT, field_names)

    def get_field_names(self, model_class):
        """Returns the audit field names stored on the audited Model class

        :param model_class: a Django Model class under audit
        """
        return getattr(model_class, self.ATTACH_FIELD_NAMES_AT)

    def get_field_value(self, instance, field_name, bootstrap=False):
        """Returns the database value of a field on ``instance``.

        :param instance: an instance of a Django model
        :param field_name: name of a field on ``instance``
        """
        field = instance._meta.get_field(field_name)

        if isinstance(field, models.ManyToManyField):
            # ManyToManyField handled by Django signals
            if bootstrap:
                return self.get_m2m_field_value(instance, field_name)
            return []
        return field.to_python(field.value_from_object(instance))

    def attach_initial_values(self, instance):
        """Save copies of field values on an instance so they can be used later
        to determine if the instance has changed and record what the previous
        values were.

        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are already attached to
            the instance
        """
        from .models import AttachValuesError

        if hasattr(instance, self.ATTACH_INIT_VALUES_AT):
            # This should never happen, but to be safe, refuse to clobber
            # existing attributes.
            raise AttachValuesError(
                f"refusing to overwrite {self.ATTACH_INIT_VALUES_AT!r} "
                f"on model instance: {instance}"
            )
        field_names = self.get_field_names(instance)
        init_values = {f: self.get_field_value(instance, f) for f in field_names}
        setattr(instance, self.ATTACH_INIT_VALUES_AT, init_values)

    def attach_initial_m2m_values(self, instance, field_name):
        field = instance._meta.get_field(field_name)
        if not isinstance(field, models.ManyToManyField):
            return None

        values = self.get_m2m_field_value(instance, field_name)
        init_values = getattr(
            instance, self.ATTACH_INIT_M2M_VALUES_AT, None
        ) or {}
        init_values.update({field_name: values})
        setattr(instance, self.ATTACH_INIT_M2M_VALUES_AT, init_values)

    def get_initial_m2m_values(self, instance, field_name):
        init_values = getattr(
            instance, self.ATTACH_INIT_M2M_VALUES_AT, None
        ) or {}
        return init_values.get(field_name)

    def clear_initial_m2m_field_values(self, instance, field_name):
        init_values = getattr(
            instance, self.ATTACH_INIT_M2M_VALUES_AT, None
        ) or {}
        init_values.pop(field_name, None)
        setattr(instance, self.ATTACH_INIT_M2M_VALUES_AT, init_values)

    def get_m2m_field_value(self, instance, field_name):
        if instance.pk is None:
            # Instance is not saved, return empty list
            return []
        else:
            # Instance is saved, we can access the related objects
            related_manager = getattr(instance, field_name)
            return list(related_manager.values_list('pk', flat=True))

    def reset_initial_values(self, instance):
        """Returns the previously attached "initial values" and attaches new
        values.

        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are not attached to
            the instance
        """
        from .models import AttachValuesError

        try:
            values = getattr(instance, self.ATTACH_INIT_VALUES_AT)
        except AttributeError:
            raise AttachValuesError("cannot reset values that were never set")
        delattr(instance, self.ATTACH_INIT_VALUES_AT)
        self.attach_initial_values(instance)
        return values

    def audit_field_changes(self, *args, **kw):
        """Convenience method that calls ``make_audit_event_from_instance()``
        and saves the event (if one is returned).

        All [keyword] arguments are passed directly to
        ``make_audit_event_from_instance()``, see that method for usage.
        """
        event = self.make_audit_event_from_instance(*args, **kw)
        if event is not None:
            event.save()

    def get_delta_from_instance(self, instance, is_create, is_delete):
        """
        Returns a dictionary representing the delta of an instance of a model
        being audited for changes.
        Has the side effect of calling reset_initial_values(instance)
        which grabs and updates the initial values stored on the instance.

        :param instance: instance of a Model subclass to be audited for changes
        :param is_create: whether or not the audited event creates a new DB
            record (setting ``True`` implies that ``instance`` is changing)
        :param is_delete: whether or not the audited event deletes an existing
            DB record (setting ``True`` implies that ``instance`` is changing)
        :returns: {field_name: {'old': old_value, 'new': new_value}, ...}
        :raises: ``AssertionError`` if both is_create and is_delete are true
        """
        assert not (is_create and is_delete), \
            "is_create and is_delete cannot both be true"
        fields_to_audit = self.get_field_names(instance)
        # SIDE EFFECT: fetch and reset initial values for next db write
        init_values = self.reset_initial_values(instance)
        old_values = {} if is_create else init_values
        new_values = {} if is_delete else \
            {f: self.get_field_value(instance, f) for f in fields_to_audit}
        return self.create_delta(old_values, new_values)

    def create_delta(self, old_values, new_values):
        """
        Compares two dictionaries and creates a delta between the two

        :param old_values: {field_name: field_value, ...} representing the
        values prior to a change
        :param new_values: {field_name: field_value, ...} representing the
        values after a change
        :returns: {field_name: {'old': old_value, 'new': new_value}, ...}
        :raises: ``AssertionError`` if both old_values and new_values are empty
        do not match
        """
        assert old_values or new_values, \
            "Must provide a non-empty value for either old_values or new_values"

        changed_fields = old_values.keys() if old_values else new_values.keys()
        if old_values and new_values:
            changed_fields = new_values.keys()

        delta = {}
        for field_name in changed_fields:
            if not old_values:
                delta[field_name] = {"new": new_values[field_name]}
            elif not new_values:
                delta[field_name] = {"old": old_values[field_name]}
            else:
                try:
                    old_value = old_values[field_name]
                except KeyError:
                    delta[field_name] = {"new": new_values[field_name]}
                else:
                    if old_value != new_values[field_name]:
                        delta[field_name] = {"old": old_value,
                                             "new": new_values[field_name]}
        return delta

    def make_audit_event_from_instance(self, instance, is_create, is_delete,
                                       request, object_pk=None):
        """Factory method for creating a new ``AuditEvent`` for an instance of a
        model that's being audited for changes.

        :param instance: instance of a Model subclass to be audited for changes
        :param is_create: whether or not the audited event creates a new DB
            record (setting ``True`` implies that ``instance`` is changing)
        :param is_delete: whether or not the audited event deletes an existing
            DB record (setting ``True`` implies that ``instance`` is changing)
        :param request: the request object responsible for the change (or
            ``None`` if there is no request)
        :param object_pk: (Optional) primary key of the instance. Only used when
            ``is_delete == True``, that is, when the instance itself no longer
            references its pre-delete primary key. It is ambiguous to set this
            when ``is_delete == False``, and doing so will raise an exception.
        :returns: an unsaved ``AuditEvent`` instance (or ``None`` if
            ``instance`` has not changed)
        :raises: ``ValueError`` on invalid use of the ``object_pk`` argument
        """
        if not is_delete:
            if object_pk is not None:
                raise ValueError(
                    "'object_pk' arg is ambiguous when 'is_delete == False'"
                )
            object_pk = instance.pk

        delta = self.get_delta_from_instance(instance, is_create, is_delete)
        if delta:
            return self.create_audit_event(object_pk, type(instance), delta,
                                          is_create, is_delete, request)

    def make_audit_event_from_values(self, old_values, new_values, object_pk,
                                     object_cls, request):
        """Factory method for creating a new ``AuditEvent`` based on old and new
        values.

        :param old_values: {field_name: field_value, ...} representing the
        values prior to a change
        :param new_values: {field_name: field_value, ...} representing the
        values after a change
        :param object_pk: primary key of the instance
        :param object_cls: class type of the object being audited
        :param request: the request object responsible for the change (or
            ``None`` if there is no request)
        :returns: an unsaved ``AuditEvent`` instance (or ``None`` if
            no difference between ``old_values`` and ``new_values``)
        """
        is_create = not old_values
        is_delete = not new_values
        delta = self.create_delta(old_values, new_values)
        if delta:
            return self.create_audit_event(object_pk, object_cls, delta,
                                         is_create, is_delete, request)

    def create_audit_event(self, object_pk, object_cls, delta, is_create,
                           is_delete, request):
        from .auditors import audit_dispatcher
        from .field_audit import get_audited_class_path
        from .models import AuditEvent
        change_context = audit_dispatcher.dispatch(request)
        object_cls_path = get_audited_class_path(object_cls)
        return AuditEvent(
            object_class_path=object_cls_path,
            object_pk=object_pk,
            change_context=self._change_context_db_value(change_context),
            is_create=is_create,
            is_delete=is_delete,
            delta=delta,
        )

    def bootstrap_existing_model_records(self, model_class, field_names,
                                         batch_size=BOOTSTRAP_BATCH_SIZE,
                                         iter_records=None):
        """Creates audit events for all existing records of ``model_class``.
        Database records are fetched and created in batched bulk operations
        for efficiency.

        :param model_class: a subclass of ``django.db.models.Model`` that uses
            the ``audit_fields()`` decorator.
        :param field_names: a collection of field names to include in the
            resulting audit event ``delta`` value.
        :param batch_size: (optional) create bootstrap records in batches of
            ``batch_size``. Default: ``field_audit.const.BOOTSTRAP_BATCH_SIZE``.
            Use ``None`` to disable batching.
        :param iter_records: a callable used to fetch model instances.
            If ``None`` (the default), ``.all().iterator()`` is called on the
            model's default manager.
        :returns: number of bootstrap records created
        """
        from .auditors import audit_dispatcher
        from .field_audit import get_audited_class_path
        from .models import AuditEvent

        if iter_records is None:
            iter_records = model_class._default_manager.all().iterator

        def iter_events():
            for instance in iter_records():
                delta = {}
                for field_name in field_names:
                    value = self.get_field_value(
                        instance, field_name, bootstrap=True
                    )
                    delta[field_name] = {"new": value}
                yield AuditEvent(
                    object_class_path=object_class_path,
                    object_pk=instance.pk,
                    change_context=change_context,
                    is_bootstrap=True,
                    delta=delta,
                )

        change_context = self._change_context_db_value(
            audit_dispatcher.dispatch(None)
        )
        object_class_path = get_audited_class_path(model_class)

        if batch_size is None:
            return len(AuditEvent.objects.bulk_create(iter_events()))
        # bulk_create in batches efficiently
        # see: https://docs.djangoproject.com/en/4.0/ref/models/querysets/#bulk-create  # noqa: E501
        events = iter_events()
        total = 0
        while True:
            batch = list(islice(events, batch_size))
            if not batch:
                break
            total += len(batch)
            AuditEvent.objects.bulk_create(batch, batch_size=batch_size)
        return total

    def bootstrap_top_up(self, model_class, field_names,
                         batch_size=BOOTSTRAP_BATCH_SIZE):
        """Creates audit events for existing records of ``model_class`` which
        were created prior to auditing being enabled and are lacking a bootstrap
        or create AuditEvent record.

        :param model_class: see ``bootstrap_existing_model_records``
        :param field_names: see ``bootstrap_existing_model_records``
        :param batch_size:  see ``bootstrap_existing_model_records``
            (default=field_audit.const.BOOTSTRAP_BATCH_SIZE)
        :returns: number of bootstrap records created
        """
        from .models import AuditEvent

        subquery = (
            AuditEvent.objects
            .cast_object_pks_list(model_class)
            .filter(
                models.Q(models.Q(is_bootstrap=True) | models.Q(is_create=True))
            )
        )
        # bootstrap the model records who do not match the subquery
        model_manager = model_class._default_manager
        return self.bootstrap_existing_model_records(
            model_class,
            field_names,
            batch_size=batch_size,
            iter_records=model_manager.exclude(pk__in=subquery).iterator,
        )

    def _change_context_db_value(self, value):
        return {} if value is None else value


def get_audit_service():
    """Returns the configured audit service instance.

    This can be overridden in settings by setting FIELD_AUDIT_SERVICE_CLASS.
    """
    from django.conf import settings
    from .utils import class_import_helper

    settings_attr = "FIELD_AUDIT_SERVICE_CLASS"
    try:
        class_path = getattr(settings, settings_attr)
        service_class = class_import_helper(
            class_path, f"{settings_attr!r} value", AuditService
        )
    except AttributeError:
        service_class = AuditService

    return service_class()
