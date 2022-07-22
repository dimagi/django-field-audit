from contextlib import ContextDecorator
from datetime import datetime, timedelta
from enum import Enum
from unittest.mock import patch

from django.conf import settings
from django.db import models
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings

from field_audit.auditors import audit_dispatcher
from field_audit.models import (
    USER_TYPE_PROCESS,
    USER_TYPE_REQUEST,
    USER_TYPE_TTY,
    AttachValuesError,
    AuditAction,
    AuditEvent,
    AuditingQuerySet,
    InvalidAuditActionError,
    UnsetAuditActionError,
    get_date,
    get_manager,
    validate_audit_action,
)

from .models import Aircraft, CrewMember, Flight, ModelWithAuditingManager
from .test_field_audit import override_audited_models

EVENT_REQ_FIELDS = {"object_pk": 0, "change_context": {}, "delta": {}}


class TestAuditEventManager(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.change_context = {"user_type": "User", "username": "test"}
        fields = EVENT_REQ_FIELDS.copy()
        fields["change_context"] = cls.change_context
        cls.events = [AuditEvent.objects.create(**fields)]

    def test_by_type_and_username(self):
        self.assertEqual(
            self.events,
            list(AuditEvent.objects.by_type_and_username("User", "test")),
        )


class TestDefaultAuditEventManager(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.username = "test"
        cls.tty_user = {
            "user_type": USER_TYPE_TTY,
            "username": cls.username,
        }
        cls.proc_user = {
            "user_type": USER_TYPE_PROCESS,
            "username": cls.username,
        }
        cls.req_user = {
            "user_type": USER_TYPE_REQUEST,
            "username": cls.username,
        }
        req_fields = EVENT_REQ_FIELDS.copy()
        del req_fields["change_context"]
        cls.tty_events = {
            AuditEvent.objects.create(change_context=cls.tty_user, **req_fields)
        }
        cls.proc_events = {
            AuditEvent.objects.create(change_context=cls.proc_user,
                                      **req_fields)
        }
        cls.req_events = {
            AuditEvent.objects.create(change_context=cls.req_user, **req_fields)
        }

    def test_by_system_user(self):
        self.assertEqual(
            self.tty_events.union(self.proc_events),
            set(AuditEvent.objects.by_system_user(self.username)),
        )

    def test_by_tty_user(self):
        self.assertEqual(
            self.tty_events,
            set(AuditEvent.objects.by_tty_user(self.username)),
        )

    def test_by_process_user(self):
        self.assertEqual(
            self.proc_events,
            set(AuditEvent.objects.by_process_user(self.username)),
        )

    def test_by_request_user(self):
        self.assertEqual(
            self.req_events,
            set(AuditEvent.objects.by_request_user(self.username)),
        )


class TestGetFuncs(TestCase):

    @override_settings(FIELD_AUDIT_X="tests.test_models.NotManager")
    def test_get_manager_requires_manager_subclass(self):
        with self.assertRaises(ValueError):
            get_manager("X", models.Manager)

    @override_settings(X="raise", FIELD_AUDIT_X="tests.test_models.TestManager")
    def test_get_manager_uses_field_audit_settings_namespace(self):
        self.assertIsInstance(get_manager("X", models.Manager), TestManager)

    def test_get_manager_uses_default_if_no_setting(self):
        with self.assertRaises(AttributeError):
            settings.FIELD_AUDIT_X
        self.assertIsInstance(get_manager("X", TestManager), TestManager)

    def test_get_date(self):
        then = get_date()
        self.assertLess(
            datetime.utcnow() - then,
            timedelta(seconds=1),
        )


class NotManager:
    pass


class TestManager(models.Manager):
    __test__ = False  # this is not a test


class TestModel(models.Model):
    __test__ = False  # this is not a test
    value = models.IntegerField(null=True)
    other = models.IntegerField(null=True)


class audit_field_names(ContextDecorator):
    """Temporarily sets the audit field names collection on a model class."""

    def __init__(self, model_class, field_names):
        self.model_class = model_class
        self.field_names = field_names

    def __enter__(self):
        AuditEvent.attach_field_names(self.model_class, self.field_names)

    def __exit__(self, *exc):
        delattr(self.model_class, AuditEvent.ATTACH_FIELD_NAMES_AT)


class TestAuditEvent(TestCase):

    class Error(Exception):
        pass

    class MockAuditor:

        def __init__(self, change_context):
            self._change_context = change_context

        def change_context(self, request):
            return self._change_context

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.change_context = {"user_type": "User", "username": "test"}
        # patch the auditors chain
        audit_dispatcher.auditors = [cls.MockAuditor(cls.change_context)]

    @classmethod
    def tearDownClass(cls):
        # reset the auditors chain
        audit_dispatcher.setup_auditors()
        super().tearDownClass()

    def test_get_field_value(self):
        staff = CrewMember(title="Purser")
        self.assertIsNone(AuditEvent.get_field_value(staff, "id"))
        self.assertEqual("Purser", AuditEvent.get_field_value(staff, "title"))

    def test_get_field_value_for_null_foreignkey(self):
        flight = Flight()
        self.assertIsNone(AuditEvent.get_field_value(flight, "aircraft"))

    def test_get_field_value_for_foreignkey_with_reference_value(self):
        flight = Flight(aircraft=Aircraft(id=-1, tail_number="N778UA"))
        self.assertEqual(-1, AuditEvent.get_field_value(flight, "aircraft"))

    def test_get_field_value_for_alternate_foreignkey_to_field(self):

        class FlyByTailNumber(models.Model):
            aircraft = models.ForeignKey(
                Aircraft,
                on_delete=models.CASCADE,
                to_field="tail_number",
            )

        flyby = FlyByTailNumber(aircraft=Aircraft(tail_number="CGXII"))
        self.assertEqual("CGXII", AuditEvent.get_field_value(flyby, "aircraft"))

    def test_get_field_value_uses_field_to_python_value(self):

        class CleverTitle:

            def __init__(self, title):
                self.title = title

            def __str__(self):
                return self.title

        capt = CrewMember(title=CleverTitle("Captain"))
        self.assertEqual("Captain", AuditEvent.get_field_value(capt, "title"))

    def test_event_date_default(self):
        event = AuditEvent.objects.create(**EVENT_REQ_FIELDS)
        self.assertLess(
            event.event_date - get_date(),
            timedelta(seconds=1),
        )

    def test_object_pk_is_not_nullable(self):
        req_fields = EVENT_REQ_FIELDS.copy()
        del req_fields["object_pk"]
        with self.assertRaises(IntegrityError):
            AuditEvent.objects.create(object_pk=None, **req_fields)

    def test_change_context_is_not_nullable(self):
        req_fields = EVENT_REQ_FIELDS.copy()
        del req_fields["change_context"]
        with self.assertRaises(IntegrityError):
            AuditEvent.objects.create(change_context=None, **req_fields)

    def test_delta_not_nullable(self):
        req_fields = EVENT_REQ_FIELDS.copy()
        del req_fields["delta"]
        with self.assertRaises(IntegrityError):
            AuditEvent.objects.create(delta=None, **req_fields)

    def test_is_create_defaults_false(self):
        event = AuditEvent.objects.create(**EVENT_REQ_FIELDS)
        self.assertFalse(event.is_create)

    def test_is_delete_defaults_false(self):
        event = AuditEvent.objects.create(**EVENT_REQ_FIELDS)
        self.assertFalse(event.is_delete)

    def test_is_create_and_is_delete_exclusive_constraint(self):
        event = AuditEvent.objects.create(is_create=True, **EVENT_REQ_FIELDS)
        # ^ doesn't raise
        self.assertTrue(event.is_create)
        self.assertFalse(event.is_delete)
        event = AuditEvent.objects.create(is_delete=True, **EVENT_REQ_FIELDS)
        # ^ doesn't raise
        self.assertFalse(event.is_create)
        self.assertTrue(event.is_delete)
        with self.assertRaises(IntegrityError):
            AuditEvent.objects.create(
                is_create=True,
                is_delete=True,
                **EVENT_REQ_FIELDS,
            )

    @audit_field_names(TestModel, ["id", "value"])
    def test_attach_initial_values(self):
        instance = TestModel(id=1, value=0)
        AuditEvent.attach_initial_values(instance)
        self.assertEqual(
            {"id": 1, "value": 0},
            getattr(instance, AuditEvent.ATTACH_INIT_VALUES_AT),
        )

    @audit_field_names(TestModel, ["value"])
    def test_attach_initial_values_with_existing_attr_raises(self):
        instance = TestModel()
        setattr(instance, AuditEvent.ATTACH_INIT_VALUES_AT, None)
        with self.assertRaises(AttachValuesError):
            AuditEvent.attach_initial_values(instance)

    @audit_field_names(TestModel, ["id", "value"])
    def test_reset_initial_values(self):
        instance = TestModel(id=1, value=0)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        at_prev_init_values = AuditEvent.reset_initial_values(instance)
        at_prev_reset_values = AuditEvent.reset_initial_values(instance)
        self.assertEqual({"id": 1, "value": 0}, at_prev_init_values)
        self.assertEqual({"id": 1, "value": 1}, at_prev_reset_values)

    def test_reset_initial_values_without_existing_attr_raises(self):
        instance = TestModel(id=1)
        with self.assertRaises(AttachValuesError):
            AuditEvent.reset_initial_values(instance)

    @audit_field_names(TestModel, [])
    def test_audit_field_changes_non_delete_with_object_pk_raises(self):
        instance = TestModel()
        with self.assertRaises(ValueError):
            AuditEvent.audit_field_changes(instance, False, False, None, 1)
        with self.assertRaises(ValueError):
            # is_create
            AuditEvent.audit_field_changes(instance, True, False, None, 1)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_for_no_change(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        self.assertAuditTablesEmpty()
        AuditEvent.audit_field_changes(instance, False, False, None)
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_for_existing_save(self):
        instance = TestModel(id=1, value=0)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        self.assertAuditTablesEmpty()
        with override_audited_models({TestModel: "TestModel"}):
            AuditEvent.audit_field_changes(instance, False, False, None)
        event, = AuditEvent.objects.all()
        self.assertEqual(event.object_pk, instance.pk)
        self.assertEqual(event.change_context, self.change_context)
        self.assertFalse(event.is_create)
        self.assertFalse(event.is_delete)
        self.assertEqual({"value": {"old": 0, "new": 1}}, event.delta)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_for_multiple_saves(self):
        value = 0
        instance = TestModel(id=1, value=value)
        AuditEvent.attach_initial_values(instance)
        for value in range(2):
            value += 1
            instance.value = value
            self.assertAuditTablesEmpty()
            with override_audited_models({TestModel: "TestModel"}):
                AuditEvent.audit_field_changes(instance, False, False, None)
            event, = AuditEvent.objects.all()
            self.assertEqual(event.object_class_path, "TestModel")
            self.assertEqual(event.object_pk, instance.pk)
            self.assertEqual(event.change_context, self.change_context)
            self.assertFalse(event.is_create)
            self.assertFalse(event.is_delete)
            self.assertEqual(
                {"value": {"old": value - 1, "new": value}},
                event.delta,
            )
            event.delete()

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_for_create(self):
        instance = TestModel(id=1, value=0)
        AuditEvent.attach_initial_values(instance)
        self.assertAuditTablesEmpty()
        with override_audited_models({TestModel: "TestModel"}):
            AuditEvent.audit_field_changes(instance, True, False, None)
        event, = AuditEvent.objects.all()
        self.assertEqual(event.object_class_path, "TestModel")
        self.assertEqual(event.object_pk, instance.pk)
        self.assertEqual(event.change_context, self.change_context)
        self.assertTrue(event.is_create)
        self.assertFalse(event.is_delete)
        self.assertEqual({"value": {"new": 0}}, event.delta)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_for_delete(self):
        instance = TestModel(id=1, value=0)
        AuditEvent.attach_initial_values(instance)
        self.assertAuditTablesEmpty()
        with override_audited_models({TestModel: "TestModel"}):
            AuditEvent.audit_field_changes(instance, False, True, None,
                                           object_pk=instance.pk)
        event, = AuditEvent.objects.all()
        self.assertEqual(event.object_class_path, "TestModel")
        self.assertEqual(event.object_pk, instance.pk)
        self.assertEqual(event.change_context, self.change_context)
        self.assertFalse(event.is_create)
        self.assertTrue(event.is_delete)
        self.assertEqual({"value": {"old": 0}}, event.delta)

    @audit_field_names(TestModel, ["value", "other"])
    def test_audit_field_changes_init_values_missing(self):
        instance = TestModel(id=1, value=0, other=0)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        instance.other = 1
        # simulate a missing field
        del getattr(instance, AuditEvent.ATTACH_INIT_VALUES_AT)["value"]
        self.assertAuditTablesEmpty()
        with override_audited_models({TestModel: "TestModel"}):
            AuditEvent.audit_field_changes(instance, False, False, None)
        event, = AuditEvent.objects.all()
        self.assertEqual(event.object_class_path, "TestModel")
        self.assertEqual(event.object_pk, instance.pk)
        self.assertEqual(event.change_context, self.change_context)
        self.assertFalse(event.is_create)
        self.assertFalse(event.is_delete)
        self.assertEqual(
            {"value": {"new": 1}, "other": {"old": 0, "new": 1}},
            event.delta,
        )

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_calls_get_audited_class_path(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        patch_this = "field_audit.field_audit.get_audited_class_path"
        with patch(patch_this, return_value="test.Path") as get_acp:
            AuditEvent.audit_field_changes(instance, False, False, None)
        get_acp.assert_called_once_with(TestModel)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_calls_audit_dispatcher(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        req = object()
        with (
            override_audited_models({TestModel: "TestModel"}),
            patch.object(audit_dispatcher, "dispatch", return_value={}) as dsp,
        ):
            AuditEvent.audit_field_changes(instance, False, False, req)
        dsp.assert_called_once_with(req)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_saves_dict_on_exhausted_audit_dispatcher(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        self.assertAuditTablesEmpty()
        with (
            override_audited_models({TestModel: "TestModel"}),
            patch.object(audit_dispatcher, "dispatch", return_value=None),
        ):
            AuditEvent.audit_field_changes(instance, False, False, None)
        event, = AuditEvent.objects.all()
        self.assertEqual({}, event.change_context)

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_saves_nothing_if_no_change(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        self.assertAuditTablesEmpty()
        AuditEvent.audit_field_changes(instance, False, False, None)
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_saves_nothing_on_audit_dispatch_error(self):
        def get_ch_by(*args, **kw):
            raise self.Error()
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        self.assertAuditTablesEmpty()
        with (
            patch.object(audit_dispatcher, "dispatch", side_effect=get_ch_by),
            self.assertRaises(self.Error),
        ):
            AuditEvent.audit_field_changes(instance, False, False, None)
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_audit_field_changes_saves_nothing_on_event_save_error(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        self.assertAuditTablesEmpty()
        with (
            override_audited_models({TestModel: "TestModel"}),
            patch.object(AuditEvent, "save", side_effect=self.Error()),
            self.assertRaises(self.Error),
        ):
            AuditEvent.audit_field_changes(instance, False, False, None)
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_make_audit_event_returns_unsaved_event_for_change(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        with override_audited_models({TestModel: "TestModel"}):
            self.assertIsNotNone(AuditEvent.make_audit_event(
                instance,
                False,
                False,
                None,
            ))
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_make_audit_event_returns_none_for_non_change(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        with override_audited_models({TestModel: "TestModel"}):
            self.assertIsNone(AuditEvent.make_audit_event(
                instance,
                False,
                False,
                None,
            ))
        self.assertAuditTablesEmpty()

    def assertAuditTablesEmpty(self):
        # verify that the audit-related test tables are empty
        self.assertEqual([], list(AuditEvent.objects.all()))


class TestValidateAuditAction(TestCase):

    def test_validate_audit_action_audit(self):
        self._func(audit_action=AuditAction.AUDIT)  # does not raise

    def test_validate_audit_action_ignore(self):
        self._func(audit_action=AuditAction.IGNORE)  # does not raise

    def test_validate_audit_action_raises_unsetauditactionerror(self):
        with self.assertRaises(UnsetAuditActionError):
            self._func()

    def test_validate_audit_action_raises_invalidauditactionerror(self):
        class Action(Enum):
            VALUE = object()
        with self.assertRaises(InvalidAuditActionError):
            self._func(audit_action=Action.VALUE)

    @validate_audit_action
    def _func(self, *, audit_action=None):
        pass


class TestAuditingQuerySet(TestCase):

    def test_bulk_create_audit_action_audit_is_not_implemented(self):
        queryset = AuditingQuerySet()
        with self.assertRaises(NotImplementedError):
            queryset.bulk_create([], audit_action=AuditAction.AUDIT)

    def test_bulk_create_audit_action_ignore_calls_super(self):
        queryset = AuditingQuerySet()
        items = object()
        with patch.object(models.QuerySet, "bulk_create") as super_meth:
            queryset.bulk_create(items, audit_action=AuditAction.IGNORE)
            super_meth.assert_called_with(items)

    def test_bulk_update_audit_action_audit_is_not_implemented(self):
        queryset = AuditingQuerySet()
        with self.assertRaises(NotImplementedError):
            queryset.bulk_update([], audit_action=AuditAction.AUDIT)

    def test_bulk_update_audit_action_ignore_calls_super(self):
        queryset = AuditingQuerySet()
        items = object()
        with patch.object(models.QuerySet, "bulk_update") as super_meth:
            queryset.bulk_update(items, audit_action=AuditAction.IGNORE)
            super_meth.assert_called_with(items)

    def test_delete_audit_action_audit_deletes_and_creates_audit_events(self):
        for pkey in range(2):
            ModelWithAuditingManager.objects.create(
                id=pkey,
                value="odd" if pkey % 2 else "even",
            )
        queryset = ModelWithAuditingManager.objects.filter(value="even")
        self.assertEqual([], list(AuditEvent.objects.filter(is_delete=True)))
        queryset.delete(audit_action=AuditAction.AUDIT)
        instance, = ModelWithAuditingManager.objects.all()
        self.assertEqual(1, instance.id)
        self.assertEqual("odd", instance.value)
        event, = AuditEvent.objects.filter(is_delete=True)
        self.assertEqual(0, event.object_pk)
        self.assertEqual(True, event.is_delete)
        self.assertEqual(
            "tests.models.ModelWithAuditingManager",
            event.object_class_path,
        )
        self.assertEqual(
            {"id": {"old": 0}, "value": {"old": "even"}},
            event.delta,
        )

    def test_delete_audit_action_audit_noop_with_empty_queryset(self):
        queryset = ModelWithAuditingManager.objects.all()
        self.assertEqual([], list(queryset))
        queryset.delete(audit_action=AuditAction.AUDIT)
        self.assertEqual([], list(AuditEvent.objects.filter(is_delete=True)))

    def test_delete_audit_action_ignore_calls_super(self):
        queryset = AuditingQuerySet()
        with patch.object(models.QuerySet, "delete") as super_meth:
            queryset.delete(audit_action=AuditAction.IGNORE)
            super_meth.assert_called()

    def test_update_audit_action_audit_is_not_implemented(self):
        queryset = AuditingQuerySet()
        with self.assertRaises(NotImplementedError):
            queryset.update([], audit_action=AuditAction.AUDIT)

    def test_update_audit_action_ignore_calls_super(self):
        queryset = AuditingQuerySet()
        items = object()
        with patch.object(models.QuerySet, "update") as super_meth:
            queryset.update(items, audit_action=AuditAction.IGNORE)
            super_meth.assert_called_with(items)
