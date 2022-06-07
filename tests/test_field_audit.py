import contextvars
from contextlib import contextmanager
from unittest.mock import patch

from django.db import models
from django.test import TestCase

from field_audit.field_audit import (
    AlreadyAudited,
    _audited_models,
    _decorate_db_write,
    audit_fields,
    audited_models,
    request as audit_request,
)
from field_audit.models import AuditEvent

from .models import (
    Aerodrome,
    Aircraft,
    CrewMember,
    Flight,
)


class TestFieldAudit(TestCase):

    def test_audit_fields_without_fields_raises(self):
        with self.assertRaises(ValueError):
            audit_fields()

    def test_audit_fields_on_same_model_twice_raises(self):
        self.assertNotIn(TestSubject, audited_models())
        audit_fields("value")(TestSubject)
        try:
            self.assertIn(TestSubject, audited_models())
            with self.assertRaises(AlreadyAudited):
                audit_fields("value")(TestSubject)
        finally:
            _audited_models.remove(TestSubject)

    def test_audit_fields_on_non_model_subclass_raises(self):
        with self.assertRaises(ValueError):
            @audit_fields("field")
            class Test:
                pass

    def test__decorate_db_write_for_invalid_func_raises(self):
        def invalid(self):
            pass
        with self.assertRaises(ValueError):
            invalid = _decorate_db_write(invalid, ["field"])

    def test_audit_fields_adds_audited_models(self):
        with self.restore_audited_models():
            self.assertNotIn(TestSubject, audited_models())
            audit_fields("value")(TestSubject)
            self.assertIn(TestSubject, audited_models())

    def test_audit_fields_wraps_supported_methods(self):
        with self.restore_audited_models():

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

    def test_audited_models(self):
        self.assertEqual(
            {Aerodrome, Aircraft, CrewMember, Flight},
            set(audited_models()),
        )

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
    def restore_audited_models(self):
        backup = _audited_models.copy()
        _audited_models.clear()
        try:
            yield
        finally:
            _audited_models.clear()
            _audited_models.extend(backup)


class TestSubject(models.Model):
    __test__ = False
    value = models.IntegerField(null=True)
