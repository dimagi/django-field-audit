import logging

from django.core.exceptions import ImproperlyConfigured
from django.db.migrations import RunPython
from django.utils.module_loading import import_string

from .const import BOOTSTRAP_BATCH_SIZE

log = logging.getLogger(__name__)


def class_import_helper(dotted_path, item_description, require_type=None):
    """Returns an imported class described by ``dotted_path``.

    Similar to ``django.db.models.options.Options._get_default_pk_class()``.
    It would be nice if Django had a helper function for doing this.

    :param dotted_path: Python syntax "dotted path" of class to be imported.
    :param item_description: Description for generating helpful exception
                             messages.
    :param required_type: (Optional) require that the imported class is a
                          subclass of this.
    :raises: ImproperlyConfigured, ValueError
    """
    if not isinstance(dotted_path, str):
        # Django should implement this check to avoid confusing errors
        # from `import_string()`, like:
        # AttributeError: type object 'AutoField' has no attribute 'rsplit'
        raise ValueError(
            f"invalid {item_description}: expected 'str', "
            f"got {dotted_path!r}"
        )

    try:
        class_ = import_string(dotted_path)
    except ImportError as exc:
        msg = f"failed to import {item_description}: {dotted_path!r}"
        raise ImproperlyConfigured(msg) from exc

    if require_type is not None:
        if not issubclass(class_, require_type):
            raise ValueError(
                f"invalid imported {item_description}: expected subclass of "
                f"{require_type.__name__!r}, got {class_!r}"
            )
    return class_


def get_fqcn(cls):
    """Get the full dot-delimited class path (module + qualname)

    See: https://stackoverflow.com/a/2020083
    """
    return f"{cls.__module__}.{cls.__qualname__}"


def run_bootstrap(model_class, field_names, batch_size=BOOTSTRAP_BATCH_SIZE,
                  iter_records=None, reverse_func=RunPython.noop):
    """Returns a django migration Operation which calls
    ``AuditEvent.bootstrap_existing_model_records()`` to add "migration" records
    for existing model records.

    :param model_class: see
        ``field_audit.models.AuditEvent.bootstrap_existing_model_records``
    :param field_names: see
        ``field_audit.models.AuditEvent.bootstrap_existing_model_records``
    :param batch_size: see
        ``field_audit.models.AuditEvent.bootstrap_existing_model_records``,
        (default=field_audit.const.BOOTSTRAP_BATCH_SIZE)
    :param iter_records:  see
        ``field_audit.models.AuditEvent.bootstrap_existing_model_records``
    :param reverse_func: (optional, default: ``RunPython.noop``) a callable for
        unapplying the migration. Passed directly to the returned
        ``RunPython()`` instance as the ``reverse_code`` argument.
    """
    def do_bootstrap(*args, **kwargs):
        from .models import AuditEvent
        count = AuditEvent.bootstrap_existing_model_records(
            model_class,
            field_names,
            batch_size,
            iter_records,
        )
        log.info(
            f"bootstrapped {count} audit event{'' if count == 1 else 's'} for: "
            f"{model_class._meta.app_label}.{model_class._meta.object_name}"
        )
    return RunPython(do_bootstrap, reverse_code=reverse_func)
