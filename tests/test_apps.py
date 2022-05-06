from django.apps import apps
from django.conf import settings
from django.db.models import AutoField, BigAutoField
from django.test import TestCase
from django.test.utils import override_settings

from field_audit.apps import FieldAuditConfig
from field_audit.auditors import audit_dispatcher


class TestFieldAuditConfig(TestCase):

    def test_config_get_auto_field_default_is_bigautofield(self):
        self.assertSettingIsMissing("FIELD_AUDIT_AUTO_FIELD")
        self.assertIs(BigAutoField, FieldAuditConfig.get_auto_field())

    @override_settings(DEFAULT_AUTO_FIELD="django.db.models.AutoField")
    def test_config_get_auto_field_ignores_defaultautofield_setting(self):
        self.assertSettingIsMissing("FIELD_AUDIT_AUTO_FIELD")
        self.assertIs(BigAutoField, FieldAuditConfig.get_auto_field())

    @override_settings(FIELD_AUDIT_AUTO_FIELD="django.db.models.AutoField")
    def test_config_get_auto_field_observes_fieldauditautofield_setting(self):
        self.assertIs(AutoField, FieldAuditConfig.get_auto_field())

    def assertSettingIsMissing(self, setting_name):
        with self.assertRaises(AttributeError):
            getattr(settings, setting_name)

    def test_config_ready_sets_auditors(self):
        # ensure it's not empty already
        self.assertNotEqual([], audit_dispatcher.auditors)
        try:
            audit_dispatcher.auditors = []
            apps.get_app_config("field_audit").ready()
            self.assertNotEqual([], audit_dispatcher.auditors)
        finally:
            # reset to defaults
            audit_dispatcher.setup_auditors()
