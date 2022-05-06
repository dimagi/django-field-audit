from django.apps import AppConfig
from django.conf import settings
from django.db.models import AutoField

from .utils import class_import_helper


class FieldAuditConfig(AppConfig):

    name = "field_audit"

    def ready(self):
        from .auditors import audit_dispatcher
        audit_dispatcher.setup_auditors()

    @classmethod
    def get_auto_field(cls):
        """Returns an AutoField class used to create primary keys."""
        settings_attr = "FIELD_AUDIT_AUTO_FIELD"
        auto_field_path = getattr(
            settings,
            settings_attr,
            "django.db.models.BigAutoField",
        )
        return class_import_helper(
            auto_field_path,
            f"{settings_attr!r} value",
            AutoField,
        )
