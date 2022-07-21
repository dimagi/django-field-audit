import contextvars
from functools import wraps

from django.db import models

from .utils import get_fqcn

__all__ = [
    "AlreadyAudited",
    "audit_fields",
    "get_audited_class_path",
    "get_audited_models",
    "request",
]


class AlreadyAudited(Exception):
    """Class is already audited."""


def audit_fields(*field_names, class_path=None):
    """Class decorator for auditing field changes on DB model instances.

    Use this on Model subclasses which need field change auditing.

    :param field_names: names of fields on the model that need to be audited
    :param class_path: optional name to use as the ``object_class_path`` field
        on audit events. The default (``None``) means the audited model's
        fully qualified "dot path" will be used.
    """
    def wrapper(cls):
        if cls in _audited_models:
            raise AlreadyAudited(cls)
        if not issubclass(cls, models.Model):
            raise ValueError(f"expected Model subclass, got: {cls}")
        cls.__init__ = _decorate_init(cls.__init__, field_names)
        cls.save = _decorate_db_write(cls.save, field_names)
        cls.delete = _decorate_db_write(cls.delete, field_names)
        _audited_models[cls] = get_fqcn(cls) if class_path is None else class_path  # noqa: E501
        return cls
    if not field_names:
        raise ValueError("at least one field name is required")
    return wrapper


def _decorate_init(init, field_names):
    """Decorates the "initialization" (e.g. __init__) method on Model
    subclasses. Responsible for ensuring that the initial field values are
    recorded in order to generate an audit event change delta later.

    :param init: the  method to decorate
    :param field_names: names of fields on the model that need to be audited
    """
    @wraps(init)
    def wrapper(self, *args, **kw):
        init(self, *args, **kw)
        AuditEvent.attach_initial_values(field_names, self)
    from .models import AuditEvent
    return wrapper


def _decorate_db_write(func, field_names):
    """Decorates the "database write" methods (e.g. save, delete) on Model
    subclasses. Responsible for creating an audit event when a model instance
    changes.

    :param func: the "db write" method to decorate
    :param field_names: names of fields on the model that need to be audited
    """
    @wraps(func)
    def wrapper(self, *args, **kw):
        # for details on using 'self._state', see:
        # - https://docs.djangoproject.com/en/dev/ref/models/instances/#state
        # - https://stackoverflow.com/questions/907695/
        is_create = is_save and self._state.adding
        object_pk = self.pk if is_delete else None
        ret = func(self, *args, **kw)
        AuditEvent.audit_field_changes(
            field_names,
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
    from .models import AuditEvent
    return wrapper


def get_audited_models():
    return _audited_models.copy()


def get_audited_class_path(cls):
    return _audited_models[cls]


_audited_models = {}
request = contextvars.ContextVar("request", default=None)
