"""Tests for global auditing disable feature."""
import threading
from concurrent.futures import ThreadPoolExecutor
from django.test import TestCase, override_settings

from field_audit import disable_audit, enable_audit
from field_audit.models import AuditEvent, AuditAction
from tests.models import (
    BasicAuditedModel,
    M2MAuditedModel,
    RelatedModel,
    SpecialQSAuditedModel,
)


class SettingsDisableTestCase(TestCase):
    """Test FIELD_AUDIT_ENABLED setting."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_save_disabled_via_setting(self):
        """Verify no audit events created when setting is False."""
        obj = BasicAuditedModel.objects.create(field1="test")
        obj.field1 = "updated"
        obj.save()

        # No audit events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_delete_disabled_via_setting(self):
        """Verify no audit events on delete when disabled."""
        obj = BasicAuditedModel.objects.create(field1="test")
        pk = obj.pk
        obj.delete()

        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_save_enabled_by_default(self):
        """Verify auditing works when setting not specified."""
        obj = BasicAuditedModel.objects.create(field1="test")

        # One create event should exist
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.first()
        self.assertTrue(event.is_create)


class ContextDisableTestCase(TestCase):
    """Test disable_audit() context manager."""

    def test_disable_audit_context_manager(self):
        """Verify auditing disabled within context."""
        # Create with auditing (should create event)
        obj = BasicAuditedModel.objects.create(field1="test")
        self.assertEqual(AuditEvent.objects.count(), 1)

        # Update with auditing disabled
        with disable_audit():
            obj.field1 = "updated"
            obj.save()

        # Still only one event (create)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_disable_audit_restores_after_exception(self):
        """Verify auditing re-enabled after exception in context."""
        try:
            with disable_audit():
                obj = BasicAuditedModel.objects.create(field1="test")
                raise ValueError("test exception")
        except ValueError:
            pass

        # Auditing should be re-enabled
        obj2 = BasicAuditedModel.objects.create(field1="test2")
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_nested_disable_contexts(self):
        """Verify nested disable contexts work correctly."""
        with disable_audit():
            obj1 = BasicAuditedModel.objects.create(field1="test1")

            with disable_audit():
                obj2 = BasicAuditedModel.objects.create(field1="test2")

            obj3 = BasicAuditedModel.objects.create(field1="test3")

        # No events should be created
        self.assertEqual(AuditEvent.objects.count(), 0)


class ContextEnableTestCase(TestCase):
    """Test enable_audit() context manager."""

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_enable_audit_overrides_setting(self):
        """Verify enable_audit() works when setting is False."""
        # Create without auditing (setting is False)
        obj1 = BasicAuditedModel.objects.create(field1="test1")
        self.assertEqual(AuditEvent.objects.count(), 0)

        # Enable for specific operation
        with enable_audit():
            obj2 = BasicAuditedModel.objects.create(field1="test2")

        # One event should be created
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.first()
        self.assertEqual(event.object_pk, str(obj2.pk))


class M2MDisableTestCase(TestCase):
    """Test auditing disable for M2M fields."""

    def test_m2m_disabled_via_context(self):
        """Verify M2M changes not audited when disabled."""
        obj = M2MAuditedModel.objects.create(field1="test")
        related = RelatedModel.objects.create(name="related")

        with disable_audit():
            obj.m2m_field.add(related)

        # Only create event for main object, no M2M event
        self.assertEqual(AuditEvent.objects.count(), 1)

    @override_settings(FIELD_AUDIT_ENABLED=False)
    def test_m2m_disabled_via_setting(self):
        """Verify M2M changes not audited when setting is False."""
        obj = M2MAuditedModel.objects.create(field1="test")
        related = RelatedModel.objects.create(name="related")
        obj.m2m_field.add(related)

        # No audit events
        self.assertEqual(AuditEvent.objects.count(), 0)


class QuerySetDisableTestCase(TestCase):
    """Test auditing disable for QuerySet operations."""

    def test_bulk_create_disabled(self):
        """Verify bulk_create respects global disable."""
        objs = [
            SpecialQSAuditedModel(field1=f"test{i}")
            for i in range(5)
        ]

        with disable_audit():
            SpecialQSAuditedModel.objects.bulk_create(
                objs,
                audit_action=AuditAction.AUDIT
            )

        # No audit events created
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_queryset_update_disabled(self):
        """Verify QuerySet.update respects global disable."""
        objs = [
            SpecialQSAuditedModel.objects.create(field1=f"test{i}")
            for i in range(3)
        ]
        AuditEvent.objects.all().delete()  # Clear create events

        with disable_audit():
            SpecialQSAuditedModel.objects.filter(
                pk__in=[o.pk for o in objs]
            ).update(field1="updated", audit_action=AuditAction.AUDIT)

        # No audit events for update
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_queryset_delete_disabled(self):
        """Verify QuerySet.delete respects global disable."""
        objs = [
            SpecialQSAuditedModel.objects.create(field1=f"test{i}")
            for i in range(3)
        ]
        AuditEvent.objects.all().delete()

        with disable_audit():
            SpecialQSAuditedModel.objects.all().delete(
                audit_action=AuditAction.AUDIT
            )

        self.assertEqual(AuditEvent.objects.count(), 0)


class ThreadSafetyTestCase(TestCase):
    """Test thread safety of global disable."""

    def test_disable_is_thread_local(self):
        """Verify disable in one thread doesn't affect others."""
        results = {"thread1_count": 0, "thread2_count": 0}

        def thread1_work():
            with disable_audit():
                BasicAuditedModel.objects.create(field1="thread1")
            results["thread1_count"] = AuditEvent.objects.filter(
                object_class_path__contains="BasicAuditedModel"
            ).count()

        def thread2_work():
            # No disable - should create audit event
            BasicAuditedModel.objects.create(field1="thread2")
            results["thread2_count"] = AuditEvent.objects.filter(
                object_class_path__contains="BasicAuditedModel"
            ).count()

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(thread1_work)
            f2 = executor.submit(thread2_work)
            f1.result()
            f2.result()

        # Total should be 1 (only thread2 created event)
        total = AuditEvent.objects.count()
        self.assertEqual(total, 1)


class BackwardsCompatibilityTestCase(TestCase):
    """Verify existing behavior unchanged when feature not used."""

    def test_default_behavior_unchanged(self):
        """Verify auditing still works by default."""
        # Should work exactly as before
        obj = BasicAuditedModel.objects.create(field1="test")
        self.assertEqual(AuditEvent.objects.count(), 1)

        obj.field1 = "updated"
        obj.save()
        self.assertEqual(AuditEvent.objects.count(), 2)

        obj.delete()
        self.assertEqual(AuditEvent.objects.count(), 3)

    def test_audit_action_still_works(self):
        """Verify AuditAction.IGNORE still works independently."""
        objs = [
            SpecialQSAuditedModel(field1=f"test{i}")
            for i in range(3)
        ]

        # AuditAction.IGNORE should still work
        SpecialQSAuditedModel.objects.bulk_create(
            objs,
            audit_action=AuditAction.IGNORE
        )

        self.assertEqual(AuditEvent.objects.count(), 0)


class MigrationScenarioTestCase(TestCase):
    """Test realistic data migration scenario."""

    def test_data_migration_workflow(self):
        """Simulate data migration without audit overhead."""
        # Setup: create initial data with auditing
        initial_objs = [
            BasicAuditedModel.objects.create(field1=f"initial{i}")
            for i in range(10)
        ]
        initial_event_count = AuditEvent.objects.count()
        self.assertEqual(initial_event_count, 10)

        # Migration: bulk update without auditing
        with disable_audit():
            BasicAuditedModel.objects.all().update(field1="migrated")

        # No additional events created
        self.assertEqual(AuditEvent.objects.count(), initial_event_count)

        # Verify data actually migrated
        self.assertTrue(
            all(obj.field1 == "migrated"
                for obj in BasicAuditedModel.objects.all())
        )
