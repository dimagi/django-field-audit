import warnings
from enum import Enum
from functools import wraps

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models import Expression
from django.utils import timezone

from .const import BOOTSTRAP_BATCH_SIZE
from .utils import class_import_helper

USER_TYPE_TTY = "SystemTtyOwner",
USER_TYPE_PROCESS = "SystemProcessOwner"
USER_TYPE_REQUEST = "RequestUser"


def check_engine_sqlite(engine=None):
    """Check if SQLite (or Oracle) database engines are in use. If ``engine`` is
    ``None``, check all Django engines (otherwise check only ``engine``).

    :param engine: (Optional) name of a Django database engine.
    """
    def lite_it_up(engine):
        # check if engine "flavor" is Oracle or SQLite
        # example db engine: django.db.backends.sqlite3
        # resulting flavor:                     sqlite
        return engine.split(".")[-1][:6] in {"sqlite", "oracle"}
    if engine is None:
        for db_properties in settings.DATABASES.values():
            # The following "no branch" directive prevents coverage from
            # reporting the (known) untested branch of
            # `if lite_it_up(...)->return`. There will always be an uncovered
            # branch here because tests only run on postgres _or_ sqlite, never
            # both.
            if lite_it_up(db_properties["ENGINE"]):  # pragma: no branch
                return True
    else:
        return lite_it_up(engine)
    # "no cover" note: tests only run on postgres _or_ sqlite, never both
    return False  # pragma: no cover


class AuditEventManager(models.Manager):
    """Manager for the AuditEvent model."""

    def by_model(self, model_class):
        """Filter records for a specific model.

        :param model_class: an audited Django model class
        :returns: ``QuerySet``
        """
        from .field_audit import get_audited_class_path
        return self.filter(
            object_class_path=get_audited_class_path(model_class)
        )

    def cast_object_pk_for_model(self, model_class):
        """Filter records for a specific model and add an ``as_pk_type``
        expression column containing the ``object_pk`` values cast to the PK
        type of ``model_class``.

        :param model_class: an audited Django model class
        :returns: ``QuerySet``
        """
        if type(model_class._meta.pk) is models.JSONField:
            expression = models.F("object_pk")
        else:
            expression = CastFromJson("object_pk", model_class._meta.pk)
        return self.by_model(model_class).annotate(as_pk_type=expression)

    def cast_object_pks_list(self, model_class):
        """Convenience method for getting the results of
        ``cast_object_pk_for_model(...)`` as a values list.

        Example:
        >>> SomeModel.objects.filter(pk_in=(
            AuditEvent.objects
            .filter(event_date__gte=datetime.date.today())
            .cast_object_pks_list(SomeModel)
        ))

        :param model_class: an audited Django model class
        :param flat: optional argument passed to the
            ``values_list()`` method (default=True).
        """
        return (
            self.cast_object_pk_for_model(model_class)
            .values_list("as_pk_type", flat=True)
        )

    def by_type_and_username(self, user_type, username):
        """Use the ``contains`` query (PostgreSQL and MySQL/MariaDB only) to
        query for documents with matching keys.

        If other DB flavors are configured in Django settings, support is
        defined at import time (see below)
        """
        # "no cover" note: tests only run on postgres _or_ sqlite, never both
        return self.filter(  # pragma: no cover
            change_context__contains={
                "user_type": user_type,
                "username": username,
            },
        )

    # "no branch" note: tests only run on postgres _or_ sqlite, never both
    if check_engine_sqlite():  # pragma: no branch
        _by_type_and_username = by_type_and_username

        def by_type_and_username(self, user_type, username):
            """Support SQLite (for development) and Oracle (because it comes
            along for free).

            If these DB flavors are not needed at import time, this "extra db"
            support is never defined.
            """
            if check_engine_sqlite(settings.DATABASES[self.db]["ENGINE"]):
                # Oracle and SQLite do not support `contains` queries
                # see: https://docs.djangoproject.com/en/4.0/topics/db/queries/#std:fieldlookup-jsonfield.contains  # noqa: E501
                return self.filter(
                    change_context__user_type=user_type,
                    change_context__username=username,
                )
            # "no cover" note: tests only run on postgres _or_ sqlite, never
            # both
            return self._by_type_and_username(user_type, username)  # pragma: no cover  # noqa: E501


class CastFromJson(models.functions.comparison.Cast):

    def __init__(self, expression, output_field):
        super().__init__(JsonPreCast(expression), output_field)


class JsonPreCast(models.expressions.Func):
    """A function that works on the JSON type and prepares it such that it can
    be cast to other types."""

    template = "%(expressions)s"
    arity = 1

    def as_postgresql(self, compiler, connection, **extra_context):
        sql, params = self.as_sql(compiler, connection, **extra_context)
        return f"({sql} #>> '{{}}')", params

    def as_sqlite(self, compiler, connection, **extra_context):
        sql, params = self.as_sql(compiler, connection, **extra_context)
        return f"JSON_EXTRACT({sql}, '$')", params


class DefaultAuditEventManager(AuditEventManager):
    """Default Manager for the AuditEvent model. Contains convenience methods
    for the default auditors, which may not be desirable to subclass if
    downstream projects wish to define custom auditor chains.
    """

    def by_system_user(self, username):
        system_types = [USER_TYPE_TTY, USER_TYPE_PROCESS]
        return self.filter(
            change_context__user_type__in=system_types,
            change_context__username=username,
        )

    def by_tty_user(self, username):
        return self.by_type_and_username(USER_TYPE_TTY, username)

    def by_process_user(self, username):
        return self.by_type_and_username(USER_TYPE_PROCESS, username)

    def by_request_user(self, username):
        return self.by_type_and_username(USER_TYPE_REQUEST, username)


def get_manager(attr_suffix, default):
    """Returns an instantiated manager, possibly one defined in settings.

    If the manager is defined in settings, it must be a subclass of
    ``django.db.models.Manager``.

    :param attr_suffix: Suffix (appended to ``FIELD_AUDIT_``) of settings
        attribute for the manager class path.
    :param default: Manager class. Returned if attribute isn't defined.
    """
    settings_attr = f"FIELD_AUDIT_{attr_suffix}"
    try:
        class_path = getattr(settings, settings_attr)
    except AttributeError:
        return default()
    desc = f"{settings_attr!r} value"
    return class_import_helper(class_path, desc, models.Manager)()


def get_date():
    """Returns the current UTC date/time.

    This is the "getter" for default values of the ``AuditEvent.event_date``
    field.
    """
    return timezone.now()


class AuditEvent(models.Model):
    event_date = models.DateTimeField(default=get_date, db_index=True)
    object_class_path = models.CharField(db_index=True, max_length=255)
    object_pk = models.JSONField(encoder=DjangoJSONEncoder)
    change_context = models.JSONField(encoder=DjangoJSONEncoder)
    is_create = models.BooleanField(default=False)
    is_delete = models.BooleanField(default=False)
    is_bootstrap = models.BooleanField(default=False)
    delta = models.JSONField(encoder=DjangoJSONEncoder)

    objects = get_manager("AUDITEVENT_MANAGER", DefaultAuditEventManager)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="field_audit_auditevent_chk_create_or_delete_or_bootstrap",
                check=~(
                    models.Q(is_create=True, is_delete=True) | \
                    models.Q(is_create=True, is_bootstrap=True) | \
                    models.Q(is_delete=True, is_bootstrap=True)  # noqa: E502
                ),
            ),
        ]

    @classmethod
    def attach_field_names(cls, model_class, field_names):
        """Attaches a collection of field names to a Model class for auditing.

        :param model_class: a Django Model class under audit
        :param field_names: collection of field names to audit on the model

        .. deprecated:: 1.4
            Use AuditService.attach_field_names() instead.
        """
        warnings.warn(
            "AuditEvent.attach_field_names() is deprecated. "
            "Use AuditService.attach_field_names() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.attach_field_names(model_class, field_names)

    @classmethod
    def field_names(cls, model_class):
        """Returns the audit field names stored on the audited Model class

        :param model_class: a Django Model class under audit

        .. deprecated:: 1.4
            Use AuditService.get_field_names() instead.
        """
        warnings.warn(
            "AuditEvent.field_names() is deprecated. "
            "Use AuditService.get_field_names() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.get_field_names(model_class)

    @staticmethod
    def get_field_value(instance, field_name, bootstrap=False):
        """Returns the database value of a field on ``instance``.

        :param instance: an instance of a Django model
        :param field_name: name of a field on ``instance``

        .. deprecated:: 1.4
            Use AuditService.get_field_value() instead.
        """
        warnings.warn(
            "AuditEvent.get_field_value() is deprecated. "
            "Use AuditService.get_field_value() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.get_field_value(instance, field_name, bootstrap)

    @classmethod
    def attach_initial_values(cls, instance):
        """Save copies of field values on an instance so they can be used later
        to determine if the instance has changed and record what the previous
        values were.

        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are already attached to
            the instance

        .. deprecated:: 1.4
            Use AuditService.attach_initial_values() instead.
        """
        warnings.warn(
            "AuditEvent.attach_initial_values() is deprecated. "
            "Use AuditService.attach_initial_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.attach_initial_values(instance)

    @classmethod
    def attach_initial_m2m_values(cls, instance, field_name):
        """.. deprecated:: 1.4
            Use AuditService.attach_initial_m2m_values() instead.
        """
        warnings.warn(
            "AuditEvent.attach_initial_m2m_values() is deprecated. "
            "Use AuditService.attach_initial_m2m_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.attach_initial_m2m_values(instance, field_name)

    @classmethod
    def get_initial_m2m_values(cls, instance, field_name):
        """.. deprecated:: 1.4
            Use AuditService.get_initial_m2m_values() instead.
        """
        warnings.warn(
            "AuditEvent.get_initial_m2m_values() is deprecated. "
            "Use AuditService.get_initial_m2m_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.get_initial_m2m_values(instance, field_name)

    @classmethod
    def clear_initial_m2m_field_values(cls, instance, field_name):
        """.. deprecated:: 1.4
            Use AuditService.clear_initial_m2m_field_values() instead.
        """
        warnings.warn(
            "AuditEvent.clear_initial_m2m_field_values() is deprecated. "
            "Use AuditService.clear_initial_m2m_field_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.clear_initial_m2m_field_values(instance, field_name)

    @classmethod
    def get_m2m_field_value(cls, instance, field_name):
        """.. deprecated:: 1.4
            Use AuditService.get_m2m_field_value() instead.
        """
        warnings.warn(
            "AuditEvent.get_m2m_field_value() is deprecated. "
            "Use AuditService.get_m2m_field_value() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.get_m2m_field_value(instance, field_name)

    @classmethod
    def reset_initial_values(cls, instance):
        """Returns the previously attached "initial values" and attaches new
        values.

        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are not attached to
            the instance

        .. deprecated:: 1.4
            Use AuditService.reset_initial_values() instead.
        """
        warnings.warn(
            "AuditEvent.reset_initial_values() is deprecated. "
            "Use AuditService.reset_initial_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.reset_initial_values(instance)

    @classmethod
    def audit_field_changes(cls, *args, **kw):
        """Convenience method that calls ``make_audit_event_from_instance()``
        and saves the event (if one is returned).

        All [keyword] arguments are passed directly to
        ``make_audit_event_from_instance()``, see that method for usage.

        .. deprecated:: 1.4
            Use AuditService.audit_field_changes() instead.
        """
        warnings.warn(
            "AuditEvent.audit_field_changes() is deprecated. "
            "Use AuditService.audit_field_changes() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.audit_field_changes(*args, **kw)

    @classmethod
    def get_delta_from_instance(cls, instance, is_create, is_delete):
        """
        Returns a dictionary representing the delta of an instance of a model
        being audited for changes.
        Has the side effect of calling cls.reset_initial_values(instance)
        which grabs and updates the initial values stored on the instance.

        :param instance: instance of a Model subclass to be audited for changes
        :param is_create: whether or not the audited event creates a new DB
            record (setting ``True`` implies that ``instance`` is changing)
        :param is_delete: whether or not the audited event deletes an existing
            DB record (setting ``True`` implies that ``instance`` is changing)
        :returns: {field_name: {'old': old_value, 'new': new_value}, ...}
        :raises: ``AssertionError`` if both is_create and is_delete are true

        .. deprecated:: 1.4
            Use AuditService.get_delta_from_instance() instead.
        """
        warnings.warn(
            "AuditEvent.get_delta_from_instance() is deprecated. "
            "Use AuditService.get_delta_from_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.get_delta_from_instance(instance, is_create, is_delete)

    @staticmethod
    def create_delta(old_values, new_values):
        """
        Compares two dictionaries and creates a delta between the two

        :param old_values: {field_name: field_value, ...} representing the
        values prior to a change
        :param new_values: {field_name: field_value, ...} representing the
        values after a change
        :returns: {field_name: {'old': old_value, 'new': new_value}, ...}
        :raises: ``AssertionError`` if both old_values and new_values are empty
        do not match

        .. deprecated:: 1.4
            Use AuditService.create_delta() instead.
        """
        warnings.warn(
            "AuditEvent.create_delta() is deprecated. "
            "Use AuditService.create_delta() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.create_delta(old_values, new_values)

    @classmethod
    def make_audit_event_from_instance(cls, instance, is_create, is_delete,
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

        .. deprecated:: 1.4
            Use AuditService.make_audit_event_from_instance() instead.
        """
        warnings.warn(
            "AuditEvent.make_audit_event_from_instance() is deprecated. "
            "Use AuditService.make_audit_event_from_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.make_audit_event_from_instance(
            instance, is_create, is_delete, request, object_pk
        )

    @classmethod
    def make_audit_event_from_values(cls, old_values, new_values, object_pk,
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

        .. deprecated:: 1.4
            Use AuditService.make_audit_event_from_values() instead.
        """
        warnings.warn(
            "AuditEvent.make_audit_event_from_values() is deprecated. "
            "Use AuditService.make_audit_event_from_values() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.make_audit_event_from_values(
            old_values, new_values, object_pk, object_cls, request
        )

    @classmethod
    def create_audit_event(cls, object_pk, object_cls, delta, is_create,
                           is_delete, request):
        """.. deprecated:: 1.4
            Use AuditService.create_audit_event() instead.
        """
        warnings.warn(
            "AuditEvent.create_audit_event() is deprecated. "
            "Use AuditService.create_audit_event() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.create_audit_event(
            object_pk, object_cls, delta, is_create, is_delete, request
        )

    @classmethod
    def bootstrap_existing_model_records(cls, model_class, field_names,
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

        .. deprecated:: 1.4
            Use AuditService.bootstrap_existing_model_records() instead.
        """
        warnings.warn(
            "AuditEvent.bootstrap_existing_model_records() is deprecated. "
            "Use AuditService.bootstrap_existing_model_records() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.bootstrap_existing_model_records(
            model_class, field_names, batch_size, iter_records
        )

    @classmethod
    def bootstrap_top_up(cls, model_class, field_names,
                         batch_size=BOOTSTRAP_BATCH_SIZE):
        """Creates audit events for existing records of ``model_class`` which
        were created prior to auditing being enabled and are lacking a bootstrap
        or create AuditEvent record.

        :param model_class: see ``bootstrap_existing_model_records``
        :param field_names: see ``bootstrap_existing_model_records``
        :param batch_size:  see ``bootstrap_existing_model_records``
            (default=field_audit.const.BOOTSTRAP_BATCH_SIZE)
        :returns: number of bootstrap records created

        .. deprecated:: 1.4
            Use AuditService.bootstrap_top_up() instead.
        """
        warnings.warn(
            "AuditEvent.bootstrap_top_up() is deprecated. "
            "Use AuditService.bootstrap_top_up() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        from .services import get_audit_service
        service = get_audit_service()
        return service.bootstrap_top_up(model_class, field_names, batch_size)

    @classmethod
    def _change_context_db_value(cls, value):
        return {} if value is None else value

    def __repr__(self):  # pragma: no cover
        cls_name = type(self).__name__
        return f"<{cls_name} ({self.id}, {self.object_class_path!r})>"


class AttachValuesError(Exception):
    """Attaching initial values to a Model instance failed."""


class InvalidAuditActionError(Exception):
    """A special QuerySet write method was called with non-AuditAction enum."""


class UnsetAuditActionError(Exception):
    """A special QuerySet write method was called without an audit action."""


class AuditAction(Enum):

    AUDIT = object()
    IGNORE = object()
    RAISE = object()

    def __repr__(self):  # pragma: no cover
        return f"<{type(self).__name__}.{self.name}>"


def validate_audit_action(func):
    """Decorator that performs validation on the ``audit_action`` keyword arg.

    :raises: ``InvalidAuditActionError`` or ``UnsetAuditActionError``
    """
    @wraps(func)
    def wrapper(self, *args, **kw):
        try:
            audit_action = kw["audit_action"]
        except KeyError:
            raise UnsetAuditActionError(
                f"{type(self).__name__}.{func.__name__}() requires an audit "
                "action as a keyword argument."
            )
        if audit_action not in AuditAction:
            raise InvalidAuditActionError(
                "The 'audit_action' argument must be a value of 'AuditAction', "
                f"got {type(audit_action)!r}"
            )
        if audit_action is AuditAction.RAISE:
            raise UnsetAuditActionError(
                f"{type(self).__name__}.{func.__name__}() requires an audit "
                "action"
            )
        return func(self, *args, **kw)
    return wrapper


class AuditingQuerySet(models.QuerySet):
    """A QuerySet that can perform field change auditing for bulk write methods.

    When decorating a model class with
    ``@audit_fields(..., audit_special_queryset_writes=True)``, the model's
    default manager must be a subclass of ``AuditingManager``.  Doing so
    provides the required extra auditing logic for the following methods:

    - ``bulk_create()``
    - ``bulk_update()``
    - ``delete()``
    - ``update()``

    Each of these methods has an additional required keyword argument
    ``audit_action`` which defaults to ``AuditAction.RAISE``.  When calling one
    of the above methods, the value must be explicitly set to one of:

    - ``AuditAction.AUDIT`` -- perform the database write, creating audit events
        for the changes.
    - ``AuditAction.IGNORE`` -- perform the database write without performing
        any auditing logic (pass through).

    Calling one of these methods without setting the desired audit action will
    raise an exception.
    """

    @validate_audit_action
    def bulk_create(self, objs, *, audit_action=AuditAction.RAISE, **kw):
        if audit_action is AuditAction.IGNORE or not objs:
            return super().bulk_create(objs, **kw)
        assert audit_action is AuditAction.AUDIT, audit_action

        from .field_audit import request
        request = request.get()

        with transaction.atomic(using=self.db):
            created_objs = super().bulk_create(objs, **kw)
            audit_events = []
            from .services import get_audit_service
            service = get_audit_service()
            for obj in created_objs:
                audit_events.append(
                    service.make_audit_event_from_instance(
                        obj, True, False, request))
            AuditEvent.objects.bulk_create(audit_events)
            return created_objs

    @validate_audit_action
    def bulk_update(self, *args, audit_action=AuditAction.RAISE, **kw):
        if audit_action is AuditAction.IGNORE:
            return super().bulk_update(*args, **kw)
        else:
            raise NotImplementedError(
                "Change auditing is not implemented for bulk_update()."
            )

    @validate_audit_action
    def delete(self, *, audit_action=AuditAction.RAISE):
        if audit_action is AuditAction.IGNORE:
            return super().delete()
        assert audit_action is AuditAction.AUDIT, audit_action
        from .field_audit import request
        from .services import get_audit_service
        service = get_audit_service()
        request = request.get()
        audit_events = []
        fields_to_fetch = set(service.get_field_names(self.model)) | {'pk'}
        current_values = {}
        for values_for_instance in self.values(*fields_to_fetch):
            pk = values_for_instance.pop('pk')
            current_values[pk] = values_for_instance

        for pk, current_values_for_pk in current_values.items():
            audit_event = service.make_audit_event_from_values(
                current_values_for_pk,
                {},
                pk,
                self.model,
                request
            )
            audit_events.append(audit_event)

        with transaction.atomic(using=self.db):
            value = super().delete()
            if audit_events:
                # write the audit events _after_ the delete succeeds
                AuditEvent.objects.bulk_create(audit_events)
            return value

    @validate_audit_action
    def update(self, *, audit_action=AuditAction.RAISE, **kw):
        """
        In order to determine the old and new values of the records matched by
        the queryset, a fetch of audited values for the matched records is
        performed, resulting in one fetch of the current values, one update of
        the matched records, and one bulk creation of audit events.

        NOTE: if django.db.models.Expressions are provided as update arguments,
        an additional fetch of records is performed to obtain new values.
        """
        if audit_action is AuditAction.IGNORE:
            return super().update(**kw)
        assert audit_action is AuditAction.AUDIT, audit_action

        from .services import get_audit_service
        service = get_audit_service()
        fields_to_update = set(kw.keys())
        audited_fields = set(service.get_field_names(self.model))
        fields_to_audit = fields_to_update & audited_fields
        if not fields_to_audit:
            # no audited fields are changing
            return super().update(**kw)

        new_values = {field: kw[field] for field in fields_to_audit}
        uses_expressions = any(
            [isinstance(val, Expression) for val in new_values.values()])

        old_values = {}
        values_to_fetch = fields_to_update | {"pk"}
        for value in self.values(*values_to_fetch):
            pk = value.pop('pk')
            old_values[pk] = value

        with transaction.atomic(using=self.db):
            rows = super().update(**kw)
            if uses_expressions:
                # fetch updated values to ensure audit event deltas are accurate
                # after update is performed with expressions
                new_values = {}
                for value in self.values(*values_to_fetch):
                    pk = value.pop('pk')
                    new_values[pk] = value
            else:
                new_values = {pk: new_values for pk in old_values.keys()}

            # create and write the audit events _after_ the update succeeds
            from .field_audit import request
            request = request.get()
            audit_events = []
            for pk, old_values_for_pk in old_values.items():
                audit_event = service.make_audit_event_from_values(
                    old_values_for_pk, new_values[pk], pk, self.model, request
                )
                if audit_event:
                    audit_events.append(audit_event)
            if audit_events:
                AuditEvent.objects.bulk_create(audit_events)
            return rows


AuditingManager = models.Manager.from_queryset(AuditingQuerySet)
