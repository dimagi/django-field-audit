import threading
from functools import wraps

from django.db import models


__all__ = ["AlreadyAudited", "audit_fields", "audited_models", "set_request"]


class AlreadyAudited(Exception):
    """Class is already audited."""


def audit_fields(*field_names):
    """Class decorator for auditing field changes on DB model instances.

    Use this on Model subclasses which need field change auditing.

    :param field_names: names of fields on the model that need to be audited
    """
    def wrapper(cls):
        if cls in _audited_models:
            raise AlreadyAudited(cls)
        if not issubclass(cls, models.Model):
            raise ValueError(f"expected Model subclass, got: {cls}")
        cls.__init__ = _decorate_init(cls.__init__, field_names)
        cls.save = _decorate_db_write(cls.save, field_names)
        cls.delete = _decorate_db_write(cls.delete, field_names)
        _audited_models.append(cls)
        return cls
    if not field_names:
        raise ValueError("at least one field name is required")
    return wrapper


def _decorate_init(init, field_names):
    @wraps(init)
    def wrapper(self, *args, **kw):
        init(self, *args, **kw)
        AuditEvent.attach_initial_values(field_names, self)
    from .models import AuditEvent
    return wrapper


def _decorate_db_write(func, field_names):
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
            _get_request(),
            object_pk,
        )
        return ret
    is_save = func.__name__ == "save"
    is_delete = func.__name__ == "delete"
    if not is_save and not is_delete:
        raise ValueError(f"invalid function for decoration: {func}")
    from .models import AuditEvent
    return wrapper


def _get_request():
    try:
        return _thread.request
    except AttributeError:
        return None


def audited_models():
    return _audited_models.copy()


def set_request(request):
    _thread.request = request


_audited_models = []
_thread = threading.local()
