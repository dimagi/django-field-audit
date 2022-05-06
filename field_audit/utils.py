from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


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
