"""Tests for global auditing disable feature."""
from django.test import SimpleTestCase, TestCase, override_settings

from field_audit import disable_audit, enable_audit
from field_audit.field_audit import audit_enabled, is_audit_enabled
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
        assert audit_enabled.get() is None

        obj = SimpleModel.objects.create(value="test")
        obj.value = "updated"
        obj.save()

        # No audit events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_delete_disabled_via_setting(self):
        """Verify no audit events on delete when disabled."""
        assert audit_enabled.get() is None

        obj = SimpleModel.objects.create(value="test")
        obj.delete()

        self.assertEqual(AuditEvent.objects.count(), 0)


class ContextDisableTestCase(SimpleTestCase):
    """Test disable_audit() context manager."""

    def test_disable_audit_context_manager(self):
        """Verify auditing disabled within context."""
        assert is_audit_enabled()

        # Update with auditing disabled
        with disable_audit():
            assert not is_audit_enabled()

    def test_disable_audit_restores_after_exception(self):
        """Verify auditing re-enabled after exception in context."""
        try:
            with disable_audit():
                assert not is_audit_enabled()
                raise ValueError("test exception")
        except ValueError:
            pass

        assert is_audit_enabled()

    def test_nested_disable_contexts(self):
        """Verify nested disable contexts work correctly."""
        assert is_audit_enabled()
        with disable_audit():
            assert not is_audit_enabled()

            with disable_audit():
                assert not is_audit_enabled()

            assert not is_audit_enabled()

        assert is_audit_enabled()


class ContextEnableTestCase(TestCase):
    """Test enable_audit() context manager."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_enable_audit_overrides_setting(self):
        """Verify enable_audit() works when setting is False."""
        assert not is_audit_enabled()

        with enable_audit():
            assert is_audit_enabled()


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
        objs = ModelWithAuditingManager.objects.bulk_create([
            ModelWithAuditingManager(value=f"test{i}")
            for i in range(3)
        ], audit_action=AuditAction.IGNORE)

        with disable_audit():
            ModelWithAuditingManager.objects.filter(
                pk__in=[o.pk for o in objs]
            ).update(value="updated", audit_action=AuditAction.AUDIT)

        # No audit events for update
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_queryset_delete_disabled(self):
        """Verify QuerySet.delete respects global disable."""
        ModelWithAuditingManager.objects.bulk_create([
            ModelWithAuditingManager(value=f"test{i}")
            for i in range(3)
        ], audit_action=AuditAction.IGNORE)

        with disable_audit():
            ModelWithAuditingManager.objects.all().delete(
                audit_action=AuditAction.AUDIT
            )

        self.assertEqual(AuditEvent.objects.count(), 0)
