from datetime import datetime

from django.conf import settings
from django.db import models, transaction

from .utils import class_import_helper, get_fqcn

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
            # tests only run on postgres _or_ sqlite, never both
            if lite_it_up(db_properties["ENGINE"]):  # pragma: no branch
                return True
    else:
        return lite_it_up(engine)
    # tests only run on postgres _or_ sqlite, never both
    return False  # pragma: no cover


class AuditEventManager(models.Manager):
    """Manager for the AuditEvent model."""

    def by_type_and_username(self, user_type, username):
        """Use the ``contains`` query (PostgreSQL and MySQL/MariaDB only) to
        query for documents with matching keys.

        If other DB flavors are configured in Django settings, support is
        defined at import time (see below)
        """
        # tests only run on postgres _or_ sqlite, never both
        return self.filter(  # pragma: no cover
            changed_by__contains={"user_type": user_type, "username": username},
        )

    # tests only run on postgres _or_ sqlite, never both
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
                    changed_by__user_type=user_type,
                    changed_by__username=username,
                )
            # tests only run on postgres _or_ sqlite, never both
            return self._by_type_and_username(user_type, username)  # pragma: no cover  # noqa: E501


class DefaultAuditEventManager(AuditEventManager):
    """Default Manager for the AuditEvent model. Contains convenience methods
    for the default auditors, which may not be desirable to subclass if
    downstream projects wish to define custom auditor chains.
    """

    def by_system_user(self, username):
        system_types = [USER_TYPE_TTY, USER_TYPE_PROCESS]
        return self.filter(
            changed_by__user_type__in=system_types,
            changed_by__username=username,
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
    :parma default: Manager class. Returned if attribute isn't defined.
    """
    settings_attr = f"FIELD_AUDIT_{attr_suffix}"
    try:
        class_path = getattr(settings, settings_attr)
    except AttributeError:
        return default()
    desc = f"{settings_attr!r} value"
    return class_import_helper(class_path, desc, models.Manager)()


def get_date():
    return datetime.utcnow()


class AuditEvent(models.Model):
    # id = models.BigAutoField(primary_key=True)
    event_date = models.DateTimeField(default=get_date, db_index=True)
    object_class_path = models.CharField(db_index=True, max_length=255)
    object_pk = models.JSONField()
    changed_by = models.JSONField(null=True)
    is_create = models.BooleanField(default=False)
    is_delete = models.BooleanField(default=False)

    objects = get_manager("AUDITEVENT_MANAGER", DefaultAuditEventManager)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="field_audit_auditevent_valid_create_or_delete",
                check=~models.Q(is_create=True, is_delete=True),
            ),
        ]

    ATTACH_INIT_VALUES_AT = "__field_audit_init_values"

    @staticmethod
    def get_field_value(instance, field_name):
        """Returns the value described by ``field_name``, which might be a
        simple attribute name or a Python dot-path representing an attribute
        of an attribute.

        :param instance: an instance of a Django model
        :param field_name: a attribute name or Python dot-path of attribute
            names to get from the ``instance``.
        :raises: ``ValueError`` if not ``field_name``. ``AttributeError`` raised
            by calling ``getattr()`` on the instance or any intermediate
            objects.
        """
        value = instance
        for attr in field_name.split("."):
            if not attr:
                raise ValueError(f"invalid field_name: {field_name!r}")
            value = getattr(value, attr)
        return value

    @classmethod
    def attach_initial_values(cls, field_names, instance):
        """Save copies of field values on an instance so they can be used later
        to determine if the instance has changed and record what the previous
        values were.

        :param field_names: a collection of names of fields on ``instance``
            attach for later auditing
        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are already attached to
            the instance
        """
        if hasattr(instance, cls.ATTACH_INIT_VALUES_AT):
            # This should never happen, but to be safe, refuse to clobber
            # existing attributes.
            raise AttachValuesError(
                f"refusing to overwrite {cls.ATTACH_INIT_VALUES_AT!r} "
                f"on model instance: {instance}"
            )
        init_values = {f: cls.get_field_value(instance, f) for f in field_names}
        setattr(instance, cls.ATTACH_INIT_VALUES_AT, init_values)

    @classmethod
    def reset_initial_values(cls, field_names, instance):
        try:
            values = getattr(instance, cls.ATTACH_INIT_VALUES_AT)
        except AttributeError:
            raise AttachValuesError("cannot reset values that were never set")
        delattr(instance, cls.ATTACH_INIT_VALUES_AT)
        cls.attach_initial_values(field_names, instance)
        return values

    @classmethod
    def audit_field_changes(cls, field_names, instance, is_create, is_delete,
                            request, object_pk=None):
        """Factory method for creating a new ``AuditEvent`` and related
        ``FieldChange``s for an instance of a model that's being audited for
        changes.

        :param field_names: a collection of names of fields on ``instance`` to
            audit for changes
        :param instance: instance of a Model subclass to be audited for changes
        :param is_create: whether or not the audited event creates a new DB
            record (setting ``True`` implies that ``instance`` is changing)
        :param is_delete: whether or not the audited event deletes an existing
            DB record (setting ``True`` implies that ``instance`` is changing)
        :param request: the request object responsible for the change
        :param object_pk: (Optional) primary key of the instance. Only used when
            ``is_delete`` is ``True`` -- when the instance itself no longer
            references its pre-delete primary key. It is ambiguous to set this
            when ``is_delete == False``, and doing so will raise an exception.
        :returns: the resulting ``AuditEvent`` instance
        :raises: ``ValueError`` (invalid use of ``object_pk`` argument),
            ``AttributeError`` (no attribute ``field_name`` on ``instance``)
        """

        if not is_delete:
            if object_pk is not None:
                raise ValueError(
                    "'object_pk' argument is ambiguous when 'is_delete' is "
                    "False"
                )
            object_pk = instance.pk

        def lazy_event():
            """Returns an instantiated ``AuditEvent`` instance."""
            from .auditors import audit_dispatcher
            if event is None:
                return cls(
                    object_class_path=get_fqcn(type(instance)),
                    object_pk=None if object_pk is None else object_pk,
                    changed_by=audit_dispatcher.dispatch(request),
                    is_create=is_create,
                    is_delete=is_delete,
                )
            return event

        # fetch (and reset for next db write operation) initial values
        init_values = cls.reset_initial_values(field_names, instance)
        if is_create or is_delete:
            # initial values are meaningless in these scenarios, discard them
            init_values = {}

        event = None
        changes = []
        for field_name in field_names:
            kwargs = {
                "field_name": field_name,
                "value": cls.get_field_value(instance, field_name),
                "is_create": is_create,
                "is_delete": is_delete,
            }
            if init_values:
                try:
                    kwargs["init_value"] = init_values[field_name]
                except KeyError:
                    pass
            change = FieldChange.create_if_changed(**kwargs)
            if change is not None:
                # only instantiate the top-level event once we know we need it
                event = lazy_event()
                change.event = event
                changes.append(change)
        if changes:
            with transaction.atomic():
                event.save()
                FieldChange.objects.bulk_create(changes)

    def __repr__(self):  # pragma: no cover
        cls_name = type(self).__name__
        return f"<{cls_name} ({self.id}, {self.object_class_path!r})>"


class FieldChange(models.Model):

    event = models.ForeignKey(
        AuditEvent,
        on_delete=models.CASCADE,
        related_name="changes",
    )
    field_name = models.CharField(db_index=True, max_length=127)
    delta = models.JSONField()

    objects = get_manager("FIELDCHANGE_MANAGER", models.Manager)

    class MISSING:
        pass
    MISSING = MISSING()  # a unique object that reprs nicely

    class Meta:
        unique_together = [("event", "field_name")]

    @classmethod
    def create_if_changed(cls, field_name, value, is_create, is_delete,
                          init_value=MISSING):
        """Factory method to create a new ``FieldChange`` containing the details
        of a field change on a Model object.

        :param field_name: name of the field that may have changed
        :param value: current value of the field (i.e. value present when the DB
            write (e.g. ``save()``, ``delete()``, etc) operation occurs.
        :param is_create: whether or not the change is creating ``instance``
        :param is_delete: whether or not the change is deleting ``instance``
        :param init_value: value of the field before it was changed (not used
            when either ``is_delete`` or ``is_create`` are True, providing a
            value in either scenario will raise an exception)
        :returns: new instance of a ``FieldChange`` object or ``None``
        :raises: ``ValueError``
        """
        missing = cls.MISSING  # create a local reference
        if value is missing:
            raise ValueError("'value' cannot be MISSING")
        if (is_create or is_delete) and init_value is not missing:
            raise ValueError(
                "'init_value' is mutually exclusive with 'is_create' and "
                "'is_delete'"
            )
        delta = {}
        if is_delete:
            delta["old"] = old_value = value
            new_value = missing
        else:
            if is_create or init_value is missing:
                old_value = missing
            else:
                delta["old"] = old_value = init_value
            delta["new"] = new_value = value
        if old_value == new_value:
            # no value change, don't create an instance
            return None
        return cls(field_name=field_name, delta=delta)

    def __repr__(self):  # pragma: no cover
        cls_name = type(self).__name__
        return f"<{cls_name} ({self.id}, {self.field_name!r})>"


class AttachValuesError(Exception):
    """Attaching initial values to a Model instance failed."""
