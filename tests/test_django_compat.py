import django
from django.test import TestCase

from field_audit.models import AuditEvent

from .models import ModelWithValueOnSave, SimpleModel


class TestAuditedDbWrites(TestCase):

    def test_model_delete_is_audited(self):
        self.assertNoAuditEvents()
        instance = SimpleModel.objects.create(id=0, value='test')
        AuditEvent.objects.all().delete()  # delete the create audit event
        instance.delete()
        self.assertAuditEvent(
            is_delete=True,
            delta={"id": {"old": 0}, "value": {"old": 'test'}},
        )

    def test_model_delete_with_value_on_save_is_audited(self):
        self.assertNoAuditEvents()
        instance = ModelWithValueOnSave.objects.create(id=0)
        AuditEvent.objects.all().delete()  # delete the create audit event
        instance.delete()
        self.assertAuditEvent(
            is_delete=True,
            delta={'id': {'old': 0}, 'save_count': {'old': 1}},
        )

    def test_model_save_is_audited(self):
        self.assertNoAuditEvents()
        SimpleModel(id=0).save()
        self.assertAuditEvent(
            is_create=True,
            delta={"id": {"new": 0}, "value": {"new": None}},
        )

    def test_model_save_with_value_on_save_is_audited(self):
        self.assertNoAuditEvents()
        ModelWithValueOnSave.objects.create(id=0)
        self.assertAuditEvent(
            is_create=True,
            delta={'id': {'new': 0}, 'save_count': {'new': 1}},
        )

    def test_model_multiple_saves_with_value_on_save_is_audited(self):
        self.assertNoAuditEvents()
        instance = ModelWithValueOnSave.objects.create(id=0)
        AuditEvent.objects.all().delete()  # delete the create audit event
        instance.value = 'update'
        instance.save()
        self.assertAuditEvent(
            is_create=False,
            delta={'save_count': {'old': 1, 'new': 2}},
        )

    def test_queryset_bulk_create_is_not_audited(self):
        self.assertNoAuditEvents()
        instance, = SimpleModel.objects.bulk_create([SimpleModel(id=1)])
        instance.refresh_from_db()
        self.assertIsNotNone(instance.id)
        self.assertNoAuditEvents()

    def test_queryset_bulk_update_is_not_audited(self):
        instance = SimpleModel.objects.create(id=1)
        AuditEvent.objects.all().delete()  # delete the create audit event
        self.assertIsNone(instance.value)
        instance.value = "test"
        updates = SimpleModel.objects.bulk_update([instance], ["value"])
        if django.VERSION[0] < 4:
            expected_updates = None
        else:
            expected_updates = 1
        self.assertEqual(expected_updates, updates)
        instance.refresh_from_db()
        self.assertEqual("test", instance.value)
        self.assertNoAuditEvents()

    def test_queryset_create_is_audited(self):
        self.assertNoAuditEvents()
        instance = SimpleModel.objects.create()
        self.assertAuditEvent(
            is_create=True,
            delta={"id": {"new": instance.id}, "value": {"new": None}},
        )

    def test_queryset_delete_is_not_audited(self):
        self.assertNoAuditEvents()
        instance = SimpleModel.objects.create()
        AuditEvent.objects.all().delete()  # delete the create audit event
        instance.refresh_from_db()
        self.assertIsNotNone(instance.id)
        SimpleModel.objects.all().delete()
        self.assertNoAuditEvents()

    def test_queryset_get_or_create_is_audited(self):
        self.assertNoAuditEvents()
        self.assertEqual([], list(SimpleModel.objects.all()))
        SimpleModel.objects.get_or_create(id=0)
        self.assertAuditEvent(
            is_create=True,
            delta={"id": {"new": 0}, "value": {"new": None}},
        )

    def test_queryset_update_is_not_audited(self):
        self.assertNoAuditEvents()
        instance = SimpleModel.objects.create()
        AuditEvent.objects.all().delete()  # delete the create audit event
        SimpleModel.objects.all().update(value="test")
        instance.refresh_from_db()
        self.assertEqual("test", instance.value)
        self.assertNoAuditEvents()

    def test_queryset_update_or_create_is_audited_on_create(self):
        self.assertNoAuditEvents()
        self.assertEqual([], list(SimpleModel.objects.all()))
        response = SimpleModel.objects.update_or_create({"value": "test"}, id=0)
        instance, x = response
        self.assertEqual(0, instance.id)
        self.assertEqual("test", instance.value)
        self.assertAuditEvent(
            is_create=True,
            delta={"id": {"new": 0}, "value": {"new": "test"}},
        )

    def test_queryset_update_or_create_is_audited_on_update(self):
        self.assertNoAuditEvents()
        instance = SimpleModel.objects.create()
        AuditEvent.objects.all().delete()  # delete the create audit event
        self.assertEqual([instance], list(SimpleModel.objects.all()))
        self.assertIsNone(instance.value)
        SimpleModel.objects.update_or_create({"value": "test"}, id=instance.id)
        instance.refresh_from_db()
        self.assertEqual("test", instance.value)
        self.assertAuditEvent(
            is_create=False,
            is_delete=False,
            delta={"value": {"old": None, "new": "test"}},
        )

    def assertNoAuditEvents(self):
        self.assertEqual([], list(AuditEvent.objects.all()))

    def assertAuditEvent(self, **kwargs):
        event, = AuditEvent.objects.all()
        for name, value in kwargs.items():
            self.assertEqual(value, getattr(event, name))
