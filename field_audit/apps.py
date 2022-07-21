from django.apps import AppConfig


class FieldAuditConfig(AppConfig):

    name = "field_audit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .auditors import audit_dispatcher
        audit_dispatcher.setup_auditors()
