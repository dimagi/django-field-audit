"""Tests for global auditing disable feature."""
from django.test import TestCase, override_settings

from field_audit import disable_audit, enable_audit
from field_audit.models import AuditEvent, AuditAction
from tests.models import (
    SimpleModel,
    ModelWithAuditingManager,
    CrewMember,
    Certification,
)


class SettingsDisableTestCase(TestCase):
    """Test FIELD_AUDIT_ENABLED setting."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_save_disabled_via_setting(self):
        """Verify no audit events created when setting is False."""
        obj = SimpleModel.objects.create(value="test")
        obj.value = "updated"
        obj.save()

        # No audit events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_delete_disabled_via_setting(self):
        """Verify no audit events on delete when disabled."""
        obj = SimpleModel.objects.create(value="test")
        obj.delete()

        self.assertEqual(AuditEvent.objects.count(), 0)



class ContextDisableTestCase(TestCase):
    """Test disable_audit() context manager."""

    def test_disable_audit_context_manager(self):
        """Verify auditing disabled within context."""
        # Create with auditing (should create event)
        obj = SimpleModel.objects.create(value="test")
        self.assertEqual(AuditEvent.objects.count(), 1)

        # Update with auditing disabled
        with disable_audit():
            obj.value = "updated"
            obj.save()

        # Still only one event (create)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_disable_audit_restores_after_exception(self):
        """Verify auditing re-enabled after exception in context."""
        try:
            with disable_audit():
                SimpleModel.objects.create(value="test")
                raise ValueError("test exception")
        except ValueError:
            pass

        # Auditing should be re-enabled
        SimpleModel.objects.create(value="test2")
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_nested_disable_contexts(self):
        """Verify nested disable contexts work correctly."""
        with disable_audit():
            SimpleModel.objects.create(value="test1")

            with disable_audit():
                SimpleModel.objects.create(value="test2")

            SimpleModel.objects.create(value="test3")

        # No events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)


class ContextEnableTestCase(TestCase):
    """Test enable_audit() context manager."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_enable_audit_overrides_setting(self):
        """Verify enable_audit() works when setting is False."""
        # Create without auditing (setting is False)
        SimpleModel.objects.create(value="test1")
        self.assertEqual(AuditEvent.objects.count(), 0)

        # Enable for specific operation
        with enable_audit():
            obj2 = SimpleModel.objects.create(value="test2")

        # One event should be created
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.first()
        self.assertEqual(int(event.object_pk), obj2.pk)


class M2MDisableTestCase(TestCase):
    """Test auditing disable for M2M fields."""

    def test_m2m_disabled_via_context(self):
        """Verify M2M changes not audited when disabled."""
        obj = CrewMember.objects.create(name="test", title="pilot", flight_hours=100)
        cert = Certification.objects.create(name="cert1", certification_type="type1")
        # Clear events from creation
        initial_count = AuditEvent.objects.count()

        with disable_audit():
            obj.certifications.add(cert)

        # No M2M event should be added
        self.assertEqual(AuditEvent.objects.count(), initial_count)

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_m2m_disabled_via_setting(self):
        """Verify M2M changes not audited when setting is False."""
        obj = CrewMember.objects.create(name="test", title="pilot", flight_hours=100)
        cert = Certification.objects.create(name="cert1", certification_type="type1")
        obj.certifications.add(cert)

        # No audit events
        self.assertEqual(AuditEvent.objects.count(), 0)


class QuerySetDisableTestCase(TestCase):
    """Test auditing disable for QuerySet operations."""

    def test_bulk_create_disabled(self):
        """Verify bulk_create respects global disable."""
        objs = [
            ModelWithAuditingManager(value=f"test{i}")
            for i in range(5)
        ]

        with disable_audit():
            ModelWithAuditingManager.objects.bulk_create(
                objs,
                audit_action=AuditAction.AUDIT
            )

        # No audit events created
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_queryset_update_disabled(self):
        """Verify QuerySet.update respects global disable."""
        objs = [
            ModelWithAuditingManager.objects.create(value=f"test{i}")
            for i in range(3)
        ]
        AuditEvent.objects.all().delete()  # Clear create events

        with disable_audit():
            ModelWithAuditingManager.objects.filter(
                pk__in=[o.pk for o in objs]
            ).update(value="updated", audit_action=AuditAction.AUDIT)

        # No audit events for update
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_queryset_delete_disabled(self):
        """Verify QuerySet.delete respects global disable."""
        for i in range(3):
            ModelWithAuditingManager.objects.create(value=f"test{i}")
        AuditEvent.objects.all().delete()

        with disable_audit():
            ModelWithAuditingManager.objects.all().delete(
                audit_action=AuditAction.AUDIT
            )

        self.assertEqual(AuditEvent.objects.count(), 0)
