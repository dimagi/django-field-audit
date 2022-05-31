import os
import json

if "DB_SETTINGS" in os.environ:
    _db_settings = json.loads(os.environ["DB_SETTINGS"])
else:
    # default to sqlite3
    _db_settings = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

DATABASES = {"default": _db_settings}

INSTALLED_APPS = [
    "field_audit",
    "tests",
]

MIDDLEWARE = [
    "field_audit.middleware.FieldAuditMiddleware"
]

SECRET_KEY = "test"

# --------------------
# field_audit settings
#
# -- Default values below, override as needed.
# FIELD_AUDIT_AUDITEVENT_MANAGER = "field_audit.models.DefaultAuditEventManager"
# FIELD_AUDIT_FIELDCHANGE_MANAGER = "django.db.models.Manager"
# FIELD_AUDIT_AUDITORS = [
#     "field_audit.auditors.RequestAuditor",
#     "field_audit.auditors.SystemUserAuditor",
# ]
