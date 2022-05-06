from getpass import getuser
from subprocess import DEVNULL, CalledProcessError, check_output

from .models import (
    USER_TYPE_PROCESS,
    USER_TYPE_REQUEST,
    USER_TYPE_TTY,
)
from .utils import class_import_helper

__all__ = [
    "BaseAuditor",
    "RequestAuditor",
    "SystemUserAuditor",
    "audit_dispatcher",
]


class _AuditDispatcher:
    """Dispatcher for the Audit API used to get "changed by" information when
    creating new ``AuditEvent`` records (i.e. when an audited model is changed).

    An instance of this class maintains the auditor chain that defines which
    (and in what order) ``BaseAuditor`` subclass instances are used to acquire
    "changed by" information.

    The auditor chain can be customized by defining a list of 'BaseAuditor'
    subclass paths via the ``FIELD_AUDIT_AUDITORS`` settings attribute.
    """

    def setup_auditors(self):
        """Populate the auditors chain, possibly defined in settings.

        This method is called at app ready time, and must be called before the
        ``dispatch()`` method can be used.
        """
        from django.conf import settings
        auditors_attr = "FIELD_AUDIT_AUDITORS"
        if hasattr(settings, auditors_attr):
            self.auditors = []
            for auditor_path in getattr(settings, auditors_attr):
                auditor_class = class_import_helper(
                    auditor_path,
                    f"{auditors_attr!r} item",
                    BaseAuditor,
                )
                self.auditors.append(auditor_class())
        else:
            self.auditors = [RequestAuditor(), SystemUserAuditor()]

    def dispatch(self, request):
        """Cycles through the auditors chain and returns the first non-None
        value returned by a call to ``auditor.changed_by(request)``.

        :param request: Django request to be audited (or ``None``).
        :returns: JSON-serializable value (or ``None`` if chain is exhausted).
        """
        for auditor in self.auditors:
            changed_by = auditor.changed_by(request)
            if changed_by is not None:
                return changed_by
        return None


audit_dispatcher = _AuditDispatcher()


class BaseAuditor:
    """Abstract class for the Auditor API. Subclasses are used to return
    "changed by" information associated with an event which they know how to
    audit.

    BaseAuditor subclasses must define the following:
    - ``changed_by()`` method that returns a JSON-serializable object of
      information for events it knows how to audit (or ``None`` otherwise).
    """

    def changed_by(self, request):
        raise NotImplementedError("subclasses must implement 'changed_by()'")


class RequestAuditor(BaseAuditor):
    """Auditor class for getting users from authenticated requests."""

    def changed_by(self, request):
        if not request:
            # cannot provide a request user without a request
            return None
        if request.user.is_authenticated:
            return {
                "user_type": USER_TYPE_REQUEST,
                "username": request.user.username,
            }
        return None


class SystemUserAuditor(BaseAuditor):
    """Auditor class for getting OS usernames."""

    def __init__(self):
        self.has_who_bin = True

    def changed_by(self, request):
        if request:
            # never return a system user when a request is provided
            return None
        return self._changed_by()

    def _changed_by(self):
        username = None
        if self.has_who_bin:
            try:
                # get owner of STDIN file on login sessions (e.g. SSH)
                output = check_output(["who", "-m"], stderr=DEVNULL)
            except CalledProcessError:
                self.has_who_bin = False
            else:
                if output:
                    try:
                        username = output.split()[0].decode("utf-8")
                        user_type = USER_TYPE_TTY
                    except (IndexError, UnicodeDecodeError):
                        pass
        if not username:
            # no TTY user, get owner of the current process
            username = getuser()
            user_type = USER_TYPE_PROCESS
        if username:
            return {"user_type": user_type, "username": username}
        return None
