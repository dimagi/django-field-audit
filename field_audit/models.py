from datetime import datetime
from enum import Enum
from functools import wraps

from django.conf import settings
from django.db import models

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
    return datetime.utcnow()


class AuditEvent(models.Model):
    event_date = models.DateTimeField(default=get_date, db_index=True)
    object_class_path = models.CharField(db_index=True, max_length=255)
    object_pk = models.JSONField()
    change_context = models.JSONField()
    is_create = models.BooleanField(default=False)
    is_delete = models.BooleanField(default=False)
    delta = models.JSONField()

    objects = get_manager("AUDITEVENT_MANAGER", DefaultAuditEventManager)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="field_audit_auditevent_valid_create_or_delete",
                check=~models.Q(is_create=True, is_delete=True),
            ),
        ]

    ATTACH_FIELD_NAMES_AT = "__field_audit_field_names"
    ATTACH_INIT_VALUES_AT = "__field_audit_init_values"

    @classmethod
    def attach_field_names(cls, model_class, field_names):
        """Attaches a collection of field names to a Model class for auditing.

        :param model_class: a Django Model class under audit
        :param field_names: collection of field names to audit on the model
        """
        setattr(model_class, cls.ATTACH_FIELD_NAMES_AT, field_names)

    @classmethod
    def _field_names(cls, instance):
        """Returns the audit field names stored on the model instance's class

        :param instance: instance of a Model subclass being audited for changes
        """
        return getattr(instance.__class__, cls.ATTACH_FIELD_NAMES_AT)

    @staticmethod
    def get_field_value(instance, field_name):
        """Returns the database value of a field on ``instance``.

        :param instance: an instance of a Django model
        :param field_name: name of a field on ``instance``
        """
        field = instance._meta.get_field(field_name)
        return field.to_python(field.value_from_object(instance))

    @classmethod
    def attach_initial_values(cls, instance):
        """Save copies of field values on an instance so they can be used later
        to determine if the instance has changed and record what the previous
        values were.

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
        field_names = cls._field_names(instance)
        init_values = {f: cls.get_field_value(instance, f) for f in field_names}
        setattr(instance, cls.ATTACH_INIT_VALUES_AT, init_values)

    @classmethod
    def reset_initial_values(cls, instance):
        """Returns the previously attached "initial values" and attaches new
        values.

        :param instance: instance of a Model subclass to be audited for changes
        :raises: ``AttachValuesError`` if initial values are not attached to
            the instance
        """
        try:
            values = getattr(instance, cls.ATTACH_INIT_VALUES_AT)
        except AttributeError:
            raise AttachValuesError("cannot reset values that were never set")
        delattr(instance, cls.ATTACH_INIT_VALUES_AT)
        cls.attach_initial_values(instance)
        return values

    @classmethod
    def audit_field_changes(cls, *args, **kw):
        """Convenience method that calls ``make_audit_event()`` and saves the
        event (if one is returned).

        All args/kw passed directly to ``make_audit_event()``, see that method
        for usage.
        """
        event = cls.make_audit_event(*args, **kw)
        if event is not None:
            event.save()

    @classmethod
    def make_audit_event(cls, instance, is_create, is_delete,
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
        # fetch (and reset for next db write operation) initial values
        init_values = cls.reset_initial_values(instance)
        delta = {}
        for field_name in cls._field_names(instance):
            value = cls.get_field_value(instance, field_name)
            if is_create:
                delta[field_name] = {"new": value}
            elif is_delete:
                delta[field_name] = {"old": value}
            else:
                try:
                    init_value = init_values[field_name]
                except KeyError:
                    delta[field_name] = {"new": value}
                else:
                    if init_value != value:
                        delta[field_name] = {"old": init_value, "new": value}
        if delta:
            from .auditors import audit_dispatcher
            from .field_audit import get_audited_class_path
            change_context = audit_dispatcher.dispatch(request)
            return cls(
                object_class_path=get_audited_class_path(type(instance)),
                object_pk=object_pk,
                change_context={} if change_context is None else change_context,
                is_create=is_create,
                is_delete=is_delete,
                delta=delta,
            )

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
    def wrapper(self, *args, audit_action=AuditAction.RAISE, **kw):
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
        return func(self, *args, audit_action=audit_action, **kw)
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
    def bulk_create(self, *args, audit_action=AuditAction.RAISE, **kw):
        if audit_action is AuditAction.IGNORE:
            return super().bulk_create(*args, **kw)
        else:
            raise NotImplementedError(
                "Change auditing is not implemented for bulk_create()."
            )

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
        request = request.get()
        audit_events = []
        for instance in self:
            # make_audit_event() will never return None because delete=True
            audit_events.append(AuditEvent.make_audit_event(
                instance,
                False,
                True,
                request,
                instance.pk,
            ))
        value = super().delete()
        if audit_events:
            # write the audit events _after_ the delete succeeds
            AuditEvent.objects.bulk_create(audit_events)
        return value

    @validate_audit_action
    def update(self, *args, audit_action=AuditAction.RAISE, **kw):
        if audit_action is AuditAction.IGNORE:
            return super().update(*args, **kw)
        else:
            raise NotImplementedError(
                "Change auditing is not implemented for update()."
            )


AuditingManager = models.Manager.from_queryset(AuditingQuerySet)
