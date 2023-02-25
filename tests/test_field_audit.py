import contextvars
from contextlib import contextmanager
from unittest.mock import patch

from django.db import models
from django.test import TestCase

from field_audit.field_audit import (
    AlreadyAudited,
    InvalidManagerError,
    _audited_models,
    _verify_auditing_manager,
    _decorate_db_write,
    audit_fields,
    get_audited_class_path,
    get_audited_models,
    request as audit_request,
)
from field_audit.models import AuditEvent, AuditingManager

from .models import (
    Aerodrome,
    Aircraft,
    CrewMember,
    Flight,
    SimpleModel,
    ModelWithAuditingManager,
    ModelWithValueOnSave,
    PkAuto,
    PkJson,
)


class TestFieldAudit(TestCase):

    def test_audit_fields_without_fields_raises(self):
        with self.assertRaises(ValueError):
            audit_fields()

    def test_audit_fields_on_same_model_twice_raises(self):
        self.assertNotIn(TestSubject, get_audited_models())
        audit_fields("value")(TestSubject)
        try:
            self.assertIn(TestSubject, get_audited_models())
            with self.assertRaises(AlreadyAudited):
                audit_fields("value")(TestSubject)
        finally:
            _audited_models.pop(TestSubject)

    def test_audit_fields_on_non_model_subclass_raises(self):
        with self.assertRaises(ValueError):
            @audit_fields("field")
            class Test:
                pass

    def test__verify_auditing_manager_with_incorrect_manager_raises(self):
        class Item0(models.Model):
            pass
        with self.assertRaises(InvalidManagerError):
            _verify_auditing_manager(Item0)

    def test__decorate_db_write_for_invalid_func_raises(self):
        def invalid(self):
            pass
        with self.assertRaises(ValueError):
            _decorate_db_write(invalid)

    def test_audit_fields_adds_audited_models(self):
        with override_audited_models():
            self.assertNotIn(TestSubject, get_audited_models())
            audit_fields("value")(TestSubject)
            self.assertIn(TestSubject, get_audited_models())

    def test_audit_fields_wraps_supported_methods(self):
        with override_audited_models():

            @audit_fields("value")
            class Item(models.Model):
                value = models.IntegerField(null=True)

                def save(self):
                    pass

                def delete(self):
                    pass

                def unsupported(self):
                    pass

            with patch.object(AuditEvent, "attach_initial_values") as classmeth:
                item = Item()
                classmeth.assert_called_once()
            with patch.object(AuditEvent, "audit_field_changes") as classmeth:
                item.save()
                classmeth.assert_called_once()
                classmeth.reset_mock()
                item.delete()
                classmeth.assert_called_once()
                classmeth.reset_mock()
                item.unsupported()
                classmeth.assert_not_called()

    def test_audit_fields_verifies_manager_for_audit_special_queryset_writes(self):  # noqa: E501

        class Item1(models.Model):
            value = models.IntegerField()

        class Item2(models.Model):
            value = models.IntegerField()
            objects = AuditingManager()

        with override_audited_models():
            audit_fields("value")(Item1)  # doesn't raise
        with self.assertRaises(InvalidManagerError):
            audit_fields("value", audit_special_queryset_writes=True)(Item1)
        with override_audited_models():
            # doesn't raise
            audit_fields("value", audit_special_queryset_writes=True)(Item2)

    def test_get_audited_models(self):
        self.assertEqual(
            {
                Aerodrome,
                Aircraft,
                CrewMember,
                Flight,
                SimpleModel,
                ModelWithAuditingManager,
                ModelWithValueOnSave,
                PkAuto,
                PkJson,
            },
            set(get_audited_models()),
        )

    def test_get_audited_class_path(self):
        self.assertEqual(
            "tests.models.Flight",
            get_audited_class_path(Flight),
        )

    def test_get_audited_class_path_for_custom_path(self):
        with override_audited_models():
            audit_fields("value", class_path="test.Path")(TestSubject)
            self.assertEqual("test.Path", get_audited_class_path(TestSubject))

    def test_get_and_set_request(self):
        def test():
            self.assertIsNone(audit_request.get())
            request = object()
            audit_request.set(request)
            self.assertIs(request, audit_request.get())
        # run the test in a separate context to keep the test env sterile
        context = contextvars.copy_context()
        context.run(test)


@contextmanager
def override_audited_models(items={}):
    """Temporarily sets the global audited models collection and restores it
    again when the context exits.

    :param items: optional dict used to override the audited models collection
        inside the context, default is empty (clear audited models).
    """
    backup = _audited_models.copy()
    _audited_models.clear()
    _audited_models.update(items)
    try:
        yield
    finally:
        _audited_models.clear()
        _audited_models.update(backup)


class TestSubject(models.Model):
    __test__ = False
    value = models.IntegerField(null=True)
