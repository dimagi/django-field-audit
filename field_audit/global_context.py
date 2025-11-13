import contextvars
from contextlib import contextmanager

from django.conf import settings

# Context variable for enabling/disabling auditing at runtime
audit_enabled = contextvars.ContextVar("audit_enabled", default=None)


def is_audit_enabled():
    """
    Check if auditing is currently enabled.

    Returns True if auditing should proceed, False if disabled.
    Checks context variable first, then falls back to Django setting.
    """
    # Check context variable first (runtime override)
    ctx_value = audit_enabled.get()
    if ctx_value is not None:
        return ctx_value

    # Fall back to Django setting (default: True)
    return getattr(settings, "FIELD_AUDIT_ENABLED", True)


@contextmanager
def disable_audit():
    """
    Context manager to temporarily disable auditing.

    Example:
        from field_audit import disable_audit

        with disable_audit():
            # Auditing is disabled in this block
            obj.save()
            MyModel.objects.bulk_create(objects)
    """
    token = audit_enabled.set(False)
    try:
        yield
    finally:
        audit_enabled.reset(token)


@contextmanager
def enable_audit():
    """
    Context manager to explicitly enable auditing.

    Useful when FIELD_AUDIT_ENABLED=False but you need auditing
    for a specific block of code.
    """
    token = audit_enabled.set(True)
    try:
        yield
    finally:
        audit_enabled.reset(token)
