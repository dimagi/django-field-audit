from django.apps import apps
from django.test import TestCase

from field_audit.auditors import audit_dispatcher


class TestFieldAuditConfig(TestCase):

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
