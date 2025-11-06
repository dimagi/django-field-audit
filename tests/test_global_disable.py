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
        pk = obj.pk
        obj.delete()

        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_save_enabled_by_default(self):
        """Verify auditing works when setting not specified."""
        obj = SimpleModel.objects.create(value="test")

        # One create event should exist
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.first()
        self.assertTrue(event.is_create)


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
                obj = SimpleModel.objects.create(value="test")
                raise ValueError("test exception")
        except ValueError:
            pass

        # Auditing should be re-enabled
        obj2 = SimpleModel.objects.create(value="test2")
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_nested_disable_contexts(self):
        """Verify nested disable contexts work correctly."""
        with disable_audit():
            obj1 = SimpleModel.objects.create(value="test1")

            with disable_audit():
                obj2 = SimpleModel.objects.create(value="test2")

            obj3 = SimpleModel.objects.create(value="test3")

        # No events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)


class ContextEnableTestCase(TestCase):
    """Test enable_audit() context manager."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_enable_audit_overrides_setting(self):
        """Verify enable_audit() works when setting is False."""
        # Create without auditing (setting is False)
        obj1 = SimpleModel.objects.create(value="test1")
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
        objs = [
            ModelWithAuditingManager.objects.create(value=f"test{i}")
            for i in range(3)
        ]
        AuditEvent.objects.all().delete()

        with disable_audit():
            ModelWithAuditingManager.objects.all().delete(
                audit_action=AuditAction.AUDIT
            )

        self.assertEqual(AuditEvent.objects.count(), 0)


class ThreadSafetyTestCase(TestCase):
    """Test thread safety of global disable.

    Note: contextvars are designed to be thread-safe by default.
    Each thread/async task gets its own independent context.
    """

    def test_context_variable_isolation(self):
        """Verify context variable provides thread-local behavior."""
        from field_audit.field_audit import audit_enabled

        # Default state
        self.assertIsNone(audit_enabled.get())

        # Set in current context
        token = audit_enabled.set(False)
        self.assertFalse(audit_enabled.get())

        # Reset
        audit_enabled.reset(token)
        self.assertIsNone(audit_enabled.get())

        # Nested contexts work correctly
        token1 = audit_enabled.set(False)
        self.assertFalse(audit_enabled.get())

        token2 = audit_enabled.set(True)
        self.assertTrue(audit_enabled.get())

        audit_enabled.reset(token2)
        self.assertFalse(audit_enabled.get())

        audit_enabled.reset(token1)
        self.assertIsNone(audit_enabled.get())


class BackwardsCompatibilityTestCase(TestCase):
    """Verify existing behavior unchanged when feature not used."""

    def test_default_behavior_unchanged(self):
        """Verify auditing still works by default."""
        # Should work exactly as before
        initial_count = AuditEvent.objects.count()

        obj = SimpleModel.objects.create(value="test")
        self.assertEqual(AuditEvent.objects.count(), initial_count + 1)

        obj.value = "updated"
        obj.save()
        self.assertEqual(AuditEvent.objects.count(), initial_count + 2)

        obj.delete()
        self.assertEqual(AuditEvent.objects.count(), initial_count + 3)

    def test_audit_action_still_works(self):
        """Verify AuditAction.IGNORE still works independently."""
        initial_count = AuditEvent.objects.count()
        objs = [
            ModelWithAuditingManager(value=f"test{i}")
            for i in range(3)
        ]

        # AuditAction.IGNORE should still work
        ModelWithAuditingManager.objects.bulk_create(
            objs,
            audit_action=AuditAction.IGNORE
        )

        # No new audit events should be created
        self.assertEqual(AuditEvent.objects.count(), initial_count)


class MigrationScenarioTestCase(TestCase):
    """Test realistic data migration scenario."""

    def test_data_migration_workflow(self):
        """Simulate data migration without audit overhead."""
        # Setup: create initial data with auditing
        start_count = AuditEvent.objects.count()
        initial_objs = [
            SimpleModel.objects.create(value=f"initial{i}")
            for i in range(10)
        ]
        after_create_count = AuditEvent.objects.count()
        # Should have created 10 audit events
        self.assertEqual(after_create_count - start_count, 10)

        # Migration: bulk update without auditing
        with disable_audit():
            SimpleModel.objects.all().update(value="migrated")

        # No additional events created
        self.assertEqual(AuditEvent.objects.count(), after_create_count)

        # Verify data actually migrated
        self.assertTrue(
            all(obj.value == "migrated"
                for obj in SimpleModel.objects.all())
        )
