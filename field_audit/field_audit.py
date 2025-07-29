import contextvars
from functools import wraps

from django.db import models, router, transaction
from django.db.models.signals import m2m_changed

from .utils import get_fqcn

__all__ = [
    "AlreadyAudited",
    "audit_fields",
    "get_audited_class_path",
    "get_audited_models",
    "request",
]


class AlreadyAudited(Exception):
    """Model class is already audited."""


class InvalidManagerError(Exception):
    """Model class has an invalid manager."""


def audit_fields(*field_names, class_path=None, audit_special_queryset_writes=False):  # noqa: E501
    """Class decorator for auditing field changes on DB model instances.

    Use this on Model subclasses which need field change auditing.

    :param field_names: names of fields on the model that need to be audited
    :param class_path: optional name to use as the ``object_class_path`` field
        on audit events. The default (``None``) means the audited model's
        fully qualified "dot path" will be used.
    :param audit_special_queryset_writes: optional bool (default ``False``)
        enables auditing of the "special" QuerySet write methods (see below) for
        this model. Setting this to ``True`` requires the decorated model's
        default manager be an instance of ``field_audit.models.AuditingManager``
        **IMPORTANT**: These special QuerySet write methods often perform bulk
        or batched operations, and auditing their changes may impact efficiency.
    :raises: ``ValueError``, ``InvalidManagerError``

    Auditing is performed by decorating the model class's ``__init__()``,
    ``delete()`` and ``save()`` methods which provides audit events for all DB
    write operations except the "special" QuerySet write methods:

    - ``QuerySet.bulk_create()``
    - ``QuerySet.bulk_update()``
    - ``QuerySet.update()``
    - ``QuerySet.delete()``

    Using ``audit_special_queryset_writes=True`` (with the custom manager) lifts
    this limitation.
    """
    def wrapper(cls):
        if cls in _audited_models:
            raise AlreadyAudited(cls)
        if not issubclass(cls, models.Model):
            raise ValueError(f"expected Model subclass, got: {cls}")
        service.attach_field_names(cls, field_names)
        if audit_special_queryset_writes:
            _verify_auditing_manager(cls)
        cls.__init__ = _decorate_init(cls.__init__)
        cls.save = _decorate_db_write(cls.save)
        cls.delete = _decorate_db_write(cls.delete)
        cls.refresh_from_db = _decorate_refresh_from_db(cls.refresh_from_db)

        _register_m2m_signals(cls, field_names)
        _audited_models[cls] = get_fqcn(cls) if class_path is None else class_path  # noqa: E501
        return cls
    if not field_names:
        raise ValueError("at least one field name is required")
    from .services import get_audit_service
    service = get_audit_service()
    return wrapper


def _verify_auditing_manager(cls):
    """Verifies a model class is configured with an appropriate manager for
    special QuerySet write auditing.

    :param cls: a Model subclass
    :raises: ``InvalidManagerError``
    """
    from .models import AuditingManager
    # Don't assume 'cls.objects', use 'cls._default_manager' instead.
    # see: https://docs.djangoproject.com/en/4.0/topics/db/managers/#default-managers  # noqa: E501
    if not isinstance(cls._default_manager, AuditingManager):
        raise InvalidManagerError(
            "QuerySet write auditing requires an AuditingManager, got "
            f"{type(cls._default_manager)}"
        )


def _decorate_init(init):
    """Decorates the "initialization" (e.g. __init__) method on Model
    subclasses. Responsible for ensuring that the initial field values are
    recorded in order to generate an audit event change delta later.

    :param init: the  method to decorate
    """
    @wraps(init)
    def wrapper(self, *args, **kw):
        init(self, *args, **kw)
        service.attach_initial_values(self)
    from .services import get_audit_service
    service = get_audit_service()
    return wrapper


def _decorate_db_write(func):
    """Decorates the "database write" methods (e.g. save, delete) on Model
    subclasses. Responsible for creating an audit event when a model instance
    changes.

    :param func: the "db write" method to decorate
    """
    @wraps(func)
    def wrapper(self, *args, **kw):
        # for details on using 'self._state', see:
        # - https://docs.djangoproject.com/en/dev/ref/models/instances/#state
        # - https://stackoverflow.com/questions/907695/
        is_create = is_save and self._state.adding
        object_pk = self.pk if is_delete else None

        db = router.db_for_write(type(self))
        with transaction.atomic(using=db):
            ret = func(self, *args, **kw)
            service.audit_field_changes(
                self,
                is_create,
                is_delete,
                request.get(),
                object_pk,
            )
            return ret
    is_save = func.__name__ == "save"
    is_delete = func.__name__ == "delete"
    if not is_save and not is_delete:
        raise ValueError(f"invalid function for decoration: {func}")
    from .services import get_audit_service
    service = get_audit_service()
    return wrapper


def _decorate_refresh_from_db(func):
    """Decorates the "refresh from db" method on Model subclasses. This is
    necessary to ensure that all audited fields are included in the refresh
    to avoid recursively calling the refresh for deferred fields.

    :param func: the "refresh from db" method to decorate
    """
    @wraps(func)
    def wrapper(self, using=None, fields=None, **kwargs):
        if fields is not None:
            fields = set(fields) | set(service.get_field_names(self))
        func(self, using, fields, **kwargs)

    from .services import get_audit_service
    service = get_audit_service()
    return wrapper


def _register_m2m_signals(cls, field_names):
    """Register m2m_changed signal handlers for ManyToManyFields.

    :param cls: The model class being audited
    :param field_names: List of field names that are being audited
    """
    for field_name in field_names:
        try:
            field = cls._meta.get_field(field_name)
            if isinstance(field, models.ManyToManyField):
                m2m_changed.connect(
                    _m2m_changed_handler,
                    sender=field.remote_field.through,
                    weak=False
                )
        except Exception:
            # If field doesn't exist or isn't a M2M field, continue
            continue


def _m2m_changed_handler(sender, instance, action, pk_set, **kwargs):
    """Signal handler for m2m_changed to audit ManyToManyField changes.

    :param sender: The intermediate model class for the ManyToManyField
    :param instance: The instance whose many-to-many relation is updated
    :param action: A string indicating the type of update
    :param pk_set: For add/remove actions, set of primary key values
    """
    from .services import get_audit_service

    service = get_audit_service()

    if action not in ('post_add', 'post_remove', 'post_clear', 'pre_clear'):
        return

    if type(instance) not in _audited_models:
        return

    # Find which M2M field this change relates to
    m2m_field = None
    field_name = None
    for field in instance._meta.get_fields():
        if (
            isinstance(field, models.ManyToManyField) and
            hasattr(field, 'remote_field') and
            field.remote_field.through == sender
        ):
            m2m_field = field
            field_name = field.name
            break

    if not m2m_field or field_name not in service.get_field_names(instance):
        return

    if action == 'pre_clear':
        # `pk_set` not supplied for clear actions. Determine initial values
        # in the `pre_clear` event
        service.attach_initial_m2m_values(instance, field_name)
        return

    if action == 'post_clear':
        initial_values = service.get_initial_m2m_values(instance, field_name)
        if not initial_values:
            return
        delta = {field_name: {'remove': initial_values}}
    else:
        if not pk_set:
            # the change was a no-op
            return
        delta_key = 'add' if action == 'post_add' else 'remove'
        delta = {field_name: {delta_key: list(pk_set)}}

    req = request.get()
    event = service.create_audit_event(
        instance.pk, instance.__class__, delta, False, False, req
    )
    if event is not None:
        event.save()

    service.clear_initial_m2m_field_values(instance, field_name)


def get_audited_models():
    return _audited_models.copy()


def get_audited_class_path(cls):
    return _audited_models[cls]


_audited_models = {}
request = contextvars.ContextVar("request", default=None)
