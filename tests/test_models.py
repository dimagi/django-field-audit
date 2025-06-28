from contextlib import ContextDecorator
from datetime import timedelta
from enum import Enum
from unittest.mock import ANY, Mock, patch

import django
from django.apps import apps
from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Case, When, Value
from django.db.utils import IntegrityError, DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from field_audit.auditors import audit_dispatcher
from field_audit.const import BOOTSTRAP_BATCH_SIZE
from field_audit.models import (
    USER_TYPE_PROCESS,
    USER_TYPE_REQUEST,
    USER_TYPE_TTY,
    AttachValuesError,
    AuditAction,
    AuditEvent,
    AuditingQuerySet,
    CastFromJson,
    InvalidAuditActionError,
    JsonPreCast,
    UnsetAuditActionError,
    get_date,
    get_manager,
    validate_audit_action,
)
from .exceptions import (
    MakeAuditEventFromInstanceException,
    MakeAuditEventFromValuesException,
)
from .mocks import NoopAtomicTransaction
from .models import (
    Aerodrome,
    Aircraft,
    CrewMember,
    Flight,
    ModelWithAuditingManager,
    PkAuto,
    PkJson,
    SimpleModel,
)
from .test_field_audit import override_audited_models

EVENT_REQ_FIELDS = {"object_pk": 0, "change_context": {}, "delta": {}}


class TestAuditEventManager(TestCase):

    def test_by_type_and_username(self):
        fields = EVENT_REQ_FIELDS.copy()
        event1 = AuditEvent.objects.create(**fields)
        fields["change_context"] = {"user_type": "User", "username": "test"}
        event2 = AuditEvent.objects.create(**fields)
        self.assertEqual({event1, event2}, set(AuditEvent.objects.all()))
        self.assertEqual(
            [event2],
            list(AuditEvent.objects.by_type_and_username("User", "test")),
        )

    def test_by_model(self):
        self.assertAuditTablesEmpty()
        # the models used here are not important, just need two that are audited
        item0 = PkAuto.objects.create()
        item1 = PkJson.objects.create(id=1)
        self.assertEqual(
            set(it.id for it in [item0, item1]),
            set(AuditEvent.objects.values_list("object_pk", flat=True)),
        )
        queryset = AuditEvent.objects.by_model(PkAuto)
        self.assertEqual(
            [item0.id],
            list(queryset.values_list("object_pk", flat=True)),
        )

    def test_cast_object_pk_for_model(self):
        self.assertAuditTablesEmpty()
        items = []
        # add two records to the model table
        for value in range(2):
            items.append(PkJson.objects.create(id={"key": value}))
        # delete the audit record for the second model to verify the subquery
        # filters correctly
        AuditEvent.objects.filter(object_pk=items[1].pk).delete()
        # verify the model table has two records
        self.assertEqual(items, list(PkJson.objects.all().order_by("id")))
        # verify using the queryset as a subquery works and only matches one
        # model record
        queryset = (
            AuditEvent.objects
            .cast_object_pk_for_model(PkJson)
            .values_list("as_pk_type", flat=True)
        )
        self.assertEqual(
            [items[0]],
            list(PkJson.objects.filter(pk__in=queryset)),
        )

    def test_cast_object_pk_for_model_casts_to_non_json_type(self):
        self.assertAuditTablesEmpty()
        items = []
        # add two records to the model table
        for value in range(2):
            items.append(PkAuto.objects.create())
        # delete the audit record for the second model to verify the subquery
        # filters correctly
        AuditEvent.objects.filter(object_pk=items[1].pk).delete()
        # verify the model table has two records
        self.assertEqual(items, list(PkAuto.objects.all().order_by("id")))
        # verify using the queryset as a subquery works and only matches one
        # model record, which in this case has a non-json PK field
        queryset = (
            AuditEvent.objects
            .cast_object_pk_for_model(PkAuto)
            .values_list("as_pk_type", flat=True)
        )
        self.assertEqual(
            [items[0]],
            list(PkAuto.objects.filter(pk__in=queryset)),
        )

    def test_cast_object_pk_for_model_adds_expression(self):
        event_qs = AuditEvent.objects.all()
        pkeys_qs = AuditEvent.objects.cast_object_pk_for_model(PkAuto)
        self.assertEqual({}, event_qs.query.annotations)
        self.assertEqual(["as_pk_type"], list(pkeys_qs.query.annotations))

    def test_cast_object_pk_for_model_adds_col_expression_for_jsonfield(self):
        pkeys_qs = AuditEvent.objects.cast_object_pk_for_model(PkJson)
        (alias, expr), = list(pkeys_qs.query.annotations.items())
        self.assertEqual("as_pk_type", alias)
        self.assertIsInstance(expr, models.expressions.Col)

    def test_cast_object_pk_for_model_adds_cast_expression_for_autofield(self):
        pkeys_qs = AuditEvent.objects.cast_object_pk_for_model(PkAuto)
        (alias, expr), = list(pkeys_qs.query.annotations.items())
        self.assertEqual("as_pk_type", alias)
        self.assertIsInstance(expr, models.functions.comparison.Cast)

    def test_cast_object_pks_list(self):
        self.assertAuditTablesEmpty()
        pkeys = {0, 1}
        # generate some audit records
        for pkey in pkeys:
            PkAuto.objects.create(id=pkey)
        self.assertEqual(
            pkeys,
            set(AuditEvent.objects.cast_object_pks_list(PkAuto)),
        )

    def assertAuditTablesEmpty(self):
        # verify that the audit-related test tables are empty
        self.assertEqual([], list(AuditEvent.objects.all()))


class TestCastFromJson(TestCase):

    field = models.TextField()

    def test_as_postgresql(self):
        if django.VERSION[0] < 4:
            expected = (
                'SELECT "tests_pkjson"."id", '
                'CAST(("tests_pkjson"."id" #>> \'{}\') AS text) '
                'AS "alias" FROM "tests_pkjson"'
            )
        else:
            expected = (
                'SELECT "tests_pkjson"."id", '
                '(("tests_pkjson"."id" #>> \'{}\'))::text '
                'AS "alias" FROM "tests_pkjson"'
            )

        self.assertEqual(
            expected,
            sqlize(PkJson, CastFromJson("id", self.field), "postgresql"),
        )

    def test_as_sqlite(self):
        self.assertEqual(
            (
                'SELECT "tests_pkjson"."id", '
                'CAST(JSON_EXTRACT("tests_pkjson"."id", \'$\') AS text) '
                'AS "alias" FROM "tests_pkjson"'
            ),
            sqlize(PkJson, CastFromJson("id", self.field), "sqlite"),
        )


class TestJsonPreCast(TestCase):

    def test_as_postgresql(self):
        self.assertEqual(
            (
                'SELECT "tests_pkjson"."id", '
                '("tests_pkjson"."id" #>> \'{}\') '
                'AS "alias" FROM "tests_pkjson"'
            ),
            sqlize(PkJson, JsonPreCast("id"), "postgresql"),
        )

    def test_as_sqlite(self):
        self.assertEqual(
            (
                'SELECT "tests_pkjson"."id", '
                'JSON_EXTRACT("tests_pkjson"."id", \'$\') '
                'AS "alias" FROM "tests_pkjson"'
            ),
            sqlize(PkJson, JsonPreCast("id"), "sqlite"),
        )


def sqlize(model, expression, vendor, alias="alias"):
    with patch.object(connection, "vendor", vendor):
        annotate_kw = {alias: expression}
        return str(model.objects.annotate(**annotate_kw).query)


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
            timezone.now() - then,
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

        def cleanup_flyby():
            del apps.all_models["tests"]["flybytailnumber"]
            apps.clear_cache()

        self.addCleanup(cleanup_flyby)
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

    def test_is_bootstrap_defaults_false(self):
        event = AuditEvent.objects.create(**EVENT_REQ_FIELDS)
        self.assertFalse(event.is_bootstrap)

    def test_is_create_or_is_delete_or_is_bootstrap_exclusive_constraint(self):
        event = AuditEvent.objects.create(is_create=True, **EVENT_REQ_FIELDS)
        # ^ doesn't raise
        self.assertTrue(event.is_create)
        self.assertFalse(event.is_delete)
        self.assertFalse(event.is_bootstrap)
        event = AuditEvent.objects.create(is_delete=True, **EVENT_REQ_FIELDS)
        # ^ doesn't raise
        self.assertFalse(event.is_create)
        self.assertTrue(event.is_delete)
        self.assertFalse(event.is_bootstrap)
        event = AuditEvent.objects.create(is_bootstrap=True, **EVENT_REQ_FIELDS)
        # ^ doesn't raise
        self.assertFalse(event.is_create)
        self.assertFalse(event.is_delete)
        self.assertTrue(event.is_bootstrap)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            AuditEvent.objects.create(
                is_create=True,
                is_delete=True,
                **EVENT_REQ_FIELDS,
            )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            AuditEvent.objects.create(
                is_create=True,
                is_bootstrap=True,
                **EVENT_REQ_FIELDS,
            )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            AuditEvent.objects.create(
                is_delete=True,
                is_bootstrap=True,
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
    def test_make_audit_event_from_instance_returns_unsaved_event_for_change(self):  # noqa: E501
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 1
        with override_audited_models({TestModel: "TestModel"}):
            self.assertIsNotNone(AuditEvent.make_audit_event_from_instance(
                instance,
                False,
                False,
                None,
            ))
        self.assertAuditTablesEmpty()

    @audit_field_names(TestModel, ["value"])
    def test_make_audit_event_from_instance_returns_none_for_non_change(self):
        instance = TestModel(id=1)
        AuditEvent.attach_initial_values(instance)
        with override_audited_models({TestModel: "TestModel"}):
            self.assertIsNone(AuditEvent.make_audit_event_from_instance(
                instance,
                False,
                False,
                None,
            ))
        self.assertAuditTablesEmpty()

    def test_make_audit_event_from_values_returns_unsaved_event_for_change(self):  # noqa: E501
        with override_audited_models({TestModel: "TestModel"}):
            audit_event = AuditEvent.make_audit_event_from_values(
                {'f1': 'initial'}, {'f1': 'updated'}, 1, TestModel, None
            )
        self.assertIsNotNone(audit_event)
        self.assertAuditTablesEmpty()

    def test_make_audit_event_from_values_returns_none_for_non_change(self):
        audit_event = AuditEvent.make_audit_event_from_values(
            {'f1': 'initial'}, {'f1': 'initial'}, 1, TestModel, None
        )
        self.assertIsNone(audit_event)
        self.assertAuditTablesEmpty()

    def test_make_audit_event_from_values_sets_is_create_to_true_if_no_old_values(self):  # noqa: E501
        with override_audited_models({TestModel: "TestModel"}):
            audit_event = AuditEvent.make_audit_event_from_values(
                {}, {'f1': 'initial'}, 1, TestModel, None
            )
        self.assertTrue(audit_event.is_create)
        self.assertFalse(audit_event.is_delete)

    def test_make_audit_event_from_values_sets_is_delete_to_true_if_no_new_values(self):  # noqa: E501
        with override_audited_models({TestModel: "TestModel"}):
            audit_event = AuditEvent.make_audit_event_from_values(
                {'f1': 'initial'}, {}, 1, TestModel, None
            )
        self.assertFalse(audit_event.is_create)
        self.assertTrue(audit_event.is_delete)

    def test_make_audit_event_from_values_sets_both_create_and_delete_to_false_if_change(self):  # noqa: E501
        with override_audited_models({TestModel: "TestModel"}):
            audit_event = AuditEvent.make_audit_event_from_values(
                {'f1': 'initial'}, {'f1': 'updated'}, 1, TestModel, None
            )
        self.assertFalse(audit_event.is_create)
        self.assertFalse(audit_event.is_delete)

    @audit_field_names(TestModel, ["value"])
    def test_get_delta_returns_new_value_for_create(self):
        instance = TestModel(id=1, value=1)
        AuditEvent.attach_initial_values(instance)
        with override_audited_models({TestModel: "TestModel"}):
            delta = AuditEvent.get_delta_from_instance(instance, True, False)
        self.assertEqual(delta, {'value': {'new': 1}})

    @audit_field_names(TestModel, ["value"])
    def test_get_delta_from_instance_returns_old_value_for_delete(self):
        instance = TestModel(id=1, value=1)
        AuditEvent.attach_initial_values(instance)
        with override_audited_models({TestModel: "TestModel"}):
            delta = AuditEvent.get_delta_from_instance(instance, False, True)
        self.assertEqual(delta, {'value': {'old': 1}})

    @audit_field_names(TestModel, ["value"])
    def test_get_delta_from_instance_returns_old_and_new_value_for_update(self):
        instance = TestModel(id=1, value=1)
        AuditEvent.attach_initial_values(instance)
        instance.value = 2
        with override_audited_models({TestModel: "TestModel"}):
            delta = AuditEvent.get_delta_from_instance(instance, False, False)
        self.assertEqual(delta, {'value': {'old': 1, 'new': 2}})

    @audit_field_names(TestModel, ["value"])
    def test_get_delta_from_instance_raises_assertion_if_create_and_delete_both_true(self):  # noqa: E501
        instance = TestModel(id=1, value=1)
        AuditEvent.attach_initial_values(instance)
        with (override_audited_models({TestModel: "TestModel"}),
              self.assertRaises(AssertionError)):
            AuditEvent.get_delta_from_instance(instance, True, True)

    def test_create_delta_returns_new_when_old_values_is_empty(self):
        old_values = {}
        new_values = {'f1': 'initial', 'f2': 0}
        delta = AuditEvent.create_delta(old_values, new_values)
        self.assertEqual(delta, {'f1': {'new': 'initial'}, 'f2': {'new': 0}})

    def test_create_delta_returns_old_when_new_values_is_empty(self):
        old_values = {'f1': 'initial', 'f2': 0}
        new_values = {}
        delta = AuditEvent.create_delta(old_values, new_values)
        self.assertEqual(delta, {'f1': {'old': 'initial'}, 'f2': {'old': 0}})

    def test_create_delta_returns_old_and_new_when_changes(self):
        old_values = {'f1': 'initial', 'f2': 0}
        new_values = {'f1': 'updated', 'f2': 0}
        delta = AuditEvent.create_delta(old_values, new_values)
        self.assertEqual(delta, {'f1': {'old': 'initial', 'new': 'updated'}})

    def test_create_delta_returns_new_when_old_values_missing_field(self):
        old_values = {'f1': 'initial'}
        new_values = {'f1': 'updated', 'f2': 0}
        delta = AuditEvent.create_delta(old_values, new_values)
        self.assertEqual(delta, {'f1': {'old': 'initial', 'new': 'updated'},
                                 'f2': {'new': 0}})

    def test_create_delta_returns_empty_when_no_changes(self):
        old_values = {'f1': 'initial', 'f2': 0}
        new_values = {'f1': 'initial', 'f2': 0}
        delta = AuditEvent.create_delta(old_values, new_values)
        self.assertFalse(delta)

    def test_create_delta_raises_exception_if_old_and_new_values_are_empty(self):  # noqa: E501
        with self.assertRaises(AssertionError):
            AuditEvent.create_delta({}, {})

    def test__change_context_db_value_returns_empty_dict_for_none(self):
        self.assertEqual({}, AuditEvent._change_context_db_value(None))

    def test__change_context_db_value_is_passthrough_for_not_none(self):
        value = object()
        self.assertIs(value, AuditEvent._change_context_db_value(value))

    def assertAuditTablesEmpty(self):
        # verify that the audit-related test tables are empty
        self.assertEqual([], list(AuditEvent.objects.all()))


class TestValidateAuditAction(TestCase):

    def test_validate_audit_action_audit(self):
        self._func(audit_action=AuditAction.AUDIT)  # does not raise

    def test_validate_audit_action_ignore(self):
        self._func(audit_action=AuditAction.IGNORE)  # does not raise

    def test_validate_audit_action_raise(self):
        with self.assertRaises(UnsetAuditActionError) as test:
            self._func(audit_action=AuditAction.RAISE)
        self.assertEqual(
            str(test.exception),
            "TestValidateAuditAction._func() requires an audit action",
        ),

    def test_validate_audit_action_requires_keyword_argument(self):
        with self.assertRaises(UnsetAuditActionError) as test:
            self._func()
        self.assertEqual(
            str(test.exception),
            "TestValidateAuditAction._func() requires an audit action as a "
            "keyword argument.",
        ),

    def test_validate_audit_action_raises_invalidauditactionerror(self):
        class Action(Enum):
            VALUE = object()
        with self.assertRaises(InvalidAuditActionError):
            self._func(audit_action=Action.VALUE)

    @validate_audit_action
    def _func(self, *, audit_action=None):
        pass


class TestAuditingModelRollbackBehavior(TestCase):

    def test_create_rolls_back_if_audit_event_creation_fails(self):
        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_instance',
                side_effect=MakeAuditEventFromInstanceException()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            SimpleModel.objects.create(id=0)

        with self.assertRaises(SimpleModel.DoesNotExist):
            SimpleModel.objects.get(id=0)

    def test_delete_rolls_back_if_audit_event_creation_fails(self):
        self.assertEqual(0, AuditEvent.objects.all().count())
        instance = SimpleModel.objects.create(id=0, value='initial')
        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_instance',
                side_effect=MakeAuditEventFromInstanceException()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            instance.delete()

        try:
            SimpleModel.objects.get(id=0)
        except SimpleModel.DoesNotExist:
            self.fail("Failed to rollback deletion of SimpleModel instance.")

    def test_save_rolls_back_if_audit_event_creation_fails(self):
        self.assertEqual(0, AuditEvent.objects.all().count())
        instance = SimpleModel.objects.create(id=0, value='initial')
        instance.value = 'updated'
        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_instance',
                side_effect=MakeAuditEventFromInstanceException()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            instance.save()

        refetched_instance = SimpleModel.objects.get(id=0)
        self.assertEqual('initial', refetched_instance.value)

    def test_get_or_create_rolls_back_if_audit_event_creation_fails(self):
        """
        QuerySet.get_or_create wraps the create call in a transaction already,
        so we mock that transaction to ensure that the transaction in
        field_audit.py is behaving as expected
        """
        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_instance',
                side_effect=MakeAuditEventFromInstanceException()),
              patch('django.db.models.query.transaction',
                    NoopAtomicTransaction()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            SimpleModel.objects.get_or_create(id=0)

        with self.assertRaises(SimpleModel.DoesNotExist):
            SimpleModel.objects.get(id=0)

    def test_update_or_create_rolls_back_if_audit_event_creation_fails(self):
        """
        QuerySet.update_or_create wraps the create call in a transaction
        already, so we mock that transaction to ensure that the transaction in
        field_audit.py is behaving as expected
        """
        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_instance',
                side_effect=MakeAuditEventFromInstanceException()),
              patch('django.db.models.query.transaction',
                    NoopAtomicTransaction()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            SimpleModel.objects.update_or_create(id=0)

        with self.assertRaises(SimpleModel.DoesNotExist):
            SimpleModel.objects.get(id=0)


class TestAuditingQuerySetBulkCreate(TestCase):

    def test_bulk_create_audit_action_audit_creates_audit_events(self):
        objs = ModelWithAuditingManager.objects.bulk_create([
            ModelWithAuditingManager(id=1, value='initial1'),
            ModelWithAuditingManager(id=2, value='initial2')
        ], audit_action=AuditAction.AUDIT)

        self.assertEqual(len(objs), 2)
        for obj in objs:
            event, = AuditEvent.objects.filter(object_pk=obj.pk,
                                               is_create=True,
                                               is_delete=False)
            self.assertEqual(
                "tests.models.ModelWithAuditingManager",
                event.object_class_path,
            )
            self.assertEqual(
                {"id": {"new": obj.pk}, "value": {"new": obj.value}},
                event.delta,
            )

    def test_bulk_create_audit_action_audit_creates_audit_events_with_no_value(self):  # noqa: E501
        objs = ModelWithAuditingManager.objects.bulk_create([
            ModelWithAuditingManager(id=1),
            ModelWithAuditingManager(id=2)
        ], audit_action=AuditAction.AUDIT)

        for obj in objs:
            event, = AuditEvent.objects.filter(object_pk=obj.pk,
                                               is_create=True,
                                               is_delete=False)
            self.assertEqual(
                "tests.models.ModelWithAuditingManager",
                event.object_class_path,
            )
            self.assertEqual(
                {"id": {"new": obj.pk}, "value": {"new": None}},
                event.delta,
            )

    def test_bulk_create_audit_action_ignore_does_not_create_audit_events(self):
        ModelWithAuditingManager.objects.bulk_create([
            ModelWithAuditingManager(id=1, value='initial1'),
            ModelWithAuditingManager(id=2, value='initial2')
        ], audit_action=AuditAction.IGNORE)

        self.assertEqual(
            AuditEvent.objects.filter(is_create=True, is_delete=False).count(),
            0)

    def test_bulk_create_audit_action_raise_raises_exception(self):
        with self.assertRaises(UnsetAuditActionError):
            ModelWithAuditingManager.objects.bulk_create([
                ModelWithAuditingManager(id=1, value='initial1'),
            ], audit_action=AuditAction.RAISE)

    def test_bulk_create_audit_action_default_raises_exception(self):
        with self.assertRaises(UnsetAuditActionError):
            ModelWithAuditingManager.objects.bulk_create([
                ModelWithAuditingManager(id=1, value='initial1'),
            ])

    def test_bulk_create_audit_action_audit_rolls_back_if_fails(self):
        with (patch('field_audit.models.AuditEvent.make_audit_event_from_instance',  # noqa: E501
                    side_effect=MakeAuditEventFromInstanceException()),
              self.assertRaises(MakeAuditEventFromInstanceException)):
            ModelWithAuditingManager.objects.bulk_create([
                ModelWithAuditingManager(id=1, value='initial1'),
            ], audit_action=AuditAction.AUDIT)

        with self.assertRaises(ModelWithAuditingManager.DoesNotExist):
            ModelWithAuditingManager.objects.get(id=1)

    def test_bulk_create_with_no_objects_does_not_create_anything(self):
        objs = ModelWithAuditingManager.objects.bulk_create(
            [], audit_action=AuditAction.AUDIT)
        self.assertFalse(objs)
        self.assertEqual(
            AuditEvent.objects.filter(is_create=True, is_delete=False).count(),
            0)


class TestAuditingQuerySetBulkUpdate(TestCase):

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


class TestAuditingQuerySetUpdate(TestCase):

    def test_update_audit_action_audit_creates_audit_events(self):
        for pkey in range(2):
            ModelWithAuditingManager.objects.create(id=pkey, value="initial")

        queryset = ModelWithAuditingManager.objects.all()
        queryset.update(value='updated', audit_action=AuditAction.AUDIT)

        instances = ModelWithAuditingManager.objects.all()
        for instance in instances:
            self.assertEqual("updated", instance.value)
            event, = AuditEvent.objects.filter(object_pk=instance.pk,
                                               is_create=False, is_delete=False)
            self.assertEqual(
                "tests.models.ModelWithAuditingManager",
                event.object_class_path,
            )
            self.assertEqual(
                {"value": {"old": "initial", "new": "updated"}},
                event.delta,
            )

    def test_update_audit_action_audit_does_not_create_audit_events_if_audited_field_stays_the_same(self):  # noqa: E501
        ModelWithAuditingManager.objects.create(id=0, value="initial")

        queryset = ModelWithAuditingManager.objects.all()
        queryset.update(value='initial', audit_action=AuditAction.AUDIT)

        instance, = ModelWithAuditingManager.objects.all()
        self.assertEqual(
            0, AuditEvent.objects.filter(object_pk=instance.pk, is_create=False,
                                         is_delete=False).count()
        )

    def test_update_audit_action_audit_does_not_create_audit_events_if_no_audited_field_updated(self):  # noqa: E501
        ModelWithAuditingManager.objects.create(id=0, value="initial")

        queryset = ModelWithAuditingManager.objects.all()
        queryset.update(
            non_audited_field='updated', audit_action=AuditAction.AUDIT
        )

        instance, = ModelWithAuditingManager.objects.all()
        self.assertEqual(instance.value, 'initial')
        self.assertEqual(instance.non_audited_field, 'updated')
        self.assertEqual(
            0, AuditEvent.objects.filter(object_pk=instance.pk, is_create=False,
                                         is_delete=False).count()
        )

    def test_update_audit_action_ignore_does_not_create_audit_events(self):
        ModelWithAuditingManager.objects.create(id=0, value="number")
        queryset = ModelWithAuditingManager.objects.all()

        queryset.update(value='updated', audit_action=AuditAction.IGNORE)

        instance, = ModelWithAuditingManager.objects.all()
        self.assertEqual(instance.value, 'updated')
        self.assertEqual(
            0, AuditEvent.objects.filter(object_pk=instance.pk, is_create=False,
                                         is_delete=False).count()
        )

    def test_update_audit_action_raise_raises_exception(self):
        queryset = AuditingQuerySet()
        with self.assertRaises(UnsetAuditActionError):
            queryset.update(value='updated', audit_action=AuditAction.RAISE)

    def test_update_audit_action_default_raises_exception(self):
        queryset = AuditingQuerySet()
        with self.assertRaises(UnsetAuditActionError):
            queryset.update(value='updated')

    def test_update_audit_action_audit_rolls_back_if_fails(self):
        ModelWithAuditingManager.objects.create(id=0, value="initial")
        queryset = ModelWithAuditingManager.objects.all()

        with (patch('field_audit.models.AuditEvent.make_audit_event_from_values',  # noqa: E501
                    side_effect=MakeAuditEventFromValuesException()),
              self.assertRaises(MakeAuditEventFromValuesException)):
            queryset.update(value='updated', audit_action=AuditAction.AUDIT)

        instance = ModelWithAuditingManager.objects.get(id=0)
        self.assertEqual("initial", instance.value)

    def test_update_audit_action_audit_with_expressions_succeeds(self):
        update_kwargs = {}
        when_statements = []
        field = ModelWithAuditingManager._meta.get_field('value')
        for pkey in range(2):
            obj = ModelWithAuditingManager.objects.create(id=pkey,
                                                          value="initial")
            attr = Value(f'updated-{pkey}', output_field=field)
            when_statements.append(When(pk=obj.pk, then=attr))
        case_statement = Case(*when_statements, output_field=field)
        update_kwargs[field.attname] = case_statement

        queryset = ModelWithAuditingManager.objects.all()
        queryset.update(**dict(update_kwargs, audit_action=AuditAction.AUDIT))

        instances = ModelWithAuditingManager.objects.all()
        for instance in instances:
            self.assertEqual(f"updated-{instance.id}", instance.value)
            event, = AuditEvent.objects.filter(object_pk=instance.pk,
                                               is_create=False, is_delete=False)
            self.assertEqual(
                "tests.models.ModelWithAuditingManager",
                event.object_class_path)
            self.assertEqual(
                {"value": {"old": "initial", "new": f"updated-{instance.id}"}},
                event.delta)


class TestAuditingQuerySetDelete(TestCase):

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

    def test_delete_audit_action_audit_rolls_back_if_make_audit_event_fails(self):  # noqa: E501
        ModelWithAuditingManager.objects.create(id=0, value="initial")
        queryset = ModelWithAuditingManager.objects.all()

        with (patch(
                'field_audit.models.AuditEvent.make_audit_event_from_values',
                side_effect=MakeAuditEventFromValuesException()),
              self.assertRaises(MakeAuditEventFromValuesException)):
            queryset.delete(audit_action=AuditAction.AUDIT)

        try:
            ModelWithAuditingManager.objects.get(id=0)
        except ModelWithAuditingManager.DoesNotExist:
            self.fail(
                "Expected object with id=0 to exist, but test failed to roll "
                "back delete operation on ModelWithAuditingManager queryset.")

    def test_delete_audit_action_audit_rolls_back_if_audit_event_save_fails(self):  # noqa: E501
        ModelWithAuditingManager.objects.create(id=0, value="initial")
        queryset = ModelWithAuditingManager.objects.all()

        with (patch.object(AuditEvent.objects, 'bulk_create',
                           side_effect=DatabaseError),
              self.assertRaises(DatabaseError)):
            queryset.delete(audit_action=AuditAction.AUDIT)

        try:
            ModelWithAuditingManager.objects.get(id=0)
        except ModelWithAuditingManager.DoesNotExist:
            self.fail(
                "Expected object with id=0 to exist, but test failed to roll "
                "back delete operation on ModelWithAuditingManager queryset.")

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

    def test_delete_does_not_cause_recursion_error(self):
        aircraft = Aircraft.objects.create()
        object_pk = aircraft.id
        aircraft = Aircraft.objects.defer("tail_number", "make_model").get(id=object_pk)  # noqa: E501
        aircraft.delete()
        event = AuditEvent.objects.last()
        assert event.is_delete is True
        assert event.object_pk == object_pk


class TestAuditEventBootstrapping(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aerodrome_details = {
            "KIAD": 313,
            "VIDP": 777,
            "FACT": 151,
        }
        for icao, amsl in cls.aerodrome_details.items():
            Aerodrome.objects.create(
                icao=icao,
                elevation_amsl=amsl,
                amsl_unit="ft",
            )

    def test_bootstrap_existing_model_records(self):
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_count = AuditEvent.bootstrap_existing_model_records(
                Aerodrome,
                ["icao", "elevation_amsl", "amsl_unit"],
            )
            mock.assert_called_once_with(ANY, batch_size=BOOTSTRAP_BATCH_SIZE)
        bootstrap_events = AuditEvent.objects.filter(is_bootstrap=True)
        self.assertEqual(len(bootstrap_events), created_count)
        self._assert_bootstrap_records_match_setup_records(bootstrap_events)

    def test_bootstrap_existing_model_records_without_batching(self):
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_count = AuditEvent.bootstrap_existing_model_records(
                Aerodrome,
                ["icao", "elevation_amsl", "amsl_unit"],
                batch_size=None,
            )
            mock.assert_called_once_with(ANY)
        bootstrap_events = AuditEvent.objects.filter(is_bootstrap=True)
        self.assertEqual(len(bootstrap_events), created_count)
        self._assert_bootstrap_records_match_setup_records(bootstrap_events)

    def test_bootstrap_existing_model_records_with_alternate_batch_size(self):
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_count = AuditEvent.bootstrap_existing_model_records(
                Aerodrome,
                ["icao", "elevation_amsl", "amsl_unit"],
                batch_size=1,
            )
            self.assertEqual(created_count, mock.call_count)
            mock.assert_called_with(ANY, batch_size=1)
        bootstrap_events = AuditEvent.objects.filter(is_bootstrap=True)
        self.assertEqual(len(bootstrap_events), created_count)
        self._assert_bootstrap_records_match_setup_records(bootstrap_events)

    def test_bootstrap_existing_model_records_with_custom_iterator(self):

        def custom_iterator():
            first = Aerodrome.objects.first()
            yield first
            for instance in Aerodrome.objects.exclude(icao=first.icao):
                yield instance

        mock = Mock(wraps=custom_iterator)
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        created_count = AuditEvent.bootstrap_existing_model_records(
            Aerodrome,
            ["icao", "elevation_amsl", "amsl_unit"],
            iter_records=mock
        )
        mock.assert_called_once()
        bootstrap_events = AuditEvent.objects.filter(is_bootstrap=True)
        self.assertEqual(len(bootstrap_events), created_count)
        self._assert_bootstrap_records_match_setup_records(bootstrap_events)

    def _assert_bootstrap_records_match_setup_records(self, bootstrap_events):
        check_details = self.aerodrome_details.copy()
        for event in bootstrap_events:
            self.assertEqual(event.object_class_path, "tests.models.Aerodrome")
            self.assertFalse(event.is_create)
            self.assertFalse(event.is_delete)
            self.assertTrue(event.is_bootstrap)
            icao = event.delta["icao"]["new"]
            elevation_amsl = check_details.pop(icao)  # doesn't raise KeyError
            self.assertEqual(
                {
                    "icao": {"new": icao},
                    "elevation_amsl": {"new": elevation_amsl},
                    "amsl_unit": {"new": "ft"},
                },
                event.delta,
            )
        self.assertEqual({}, check_details)

    def test_bootstrap_existing_model_without_records(self):
        self.assertEqual([], list(Aircraft.objects.all()))
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_events = AuditEvent.bootstrap_existing_model_records(
                Aircraft,
                ["tail_number"],
            )
            mock.assert_not_called()
        self.assertEqual(0, created_events)
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))

    def test_bootstrap_existing_model_without_records_and_no_batching(self):
        self.assertEqual([], list(Aircraft.objects.all()))
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_events = AuditEvent.bootstrap_existing_model_records(
                Aircraft,
                ["tail_number"],
                batch_size=None,
            )
            mock.assert_called_once_with(ANY)
        self.assertEqual(0, created_events)
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))


class TestAuditEventBootstrapTopUp(TestCase):

    @classmethod
    def setUpClass(cls):
        """Setup a "partially bootstrapped" scenario and verify the correct
        model records get "topped up".

        Test DB state:
        - Three model records, (pks=[0, 1, 2])
        - Two audit event records:
            - one bootstrap record (pk=0)
            - one create record (pk=2)

        Expected "top up" bootstrap behavior:
        - Create one bootstrap record (pk==1)
        """
        super().setUpClass()
        # create the first model record
        cls.items = [PkAuto.objects.create(id=0)]
        # clear the "create" audit event to simulate a "pre-auditing" scenario
        AuditEvent.objects.all().delete()
        # bootstrap the model to create a single bootstrap record
        AuditEvent.bootstrap_existing_model_records(PkAuto, ["id"])
        # create the remaining model records
        cls.items.extend([
            PkAuto.objects.create(id=1),
            PkAuto.objects.create(id=2)
        ])
        # delete the "create" audit event for the "middle" record to simulate
        # it being created after the bootstrap but before webworker bounce
        AuditEvent.objects.filter(object_pk=1).delete()
        cls.pre_top_up = list(AuditEvent.objects.all().order_by("object_pk"))
        cls.init_bootsp, cls.init_create = cls.pre_top_up
        # top 'em up!
        cls.top_up_count = AuditEvent.bootstrap_top_up(PkAuto, ["id"])

    def test_all_setup_model_records_exist(self):
        self.assertEqual(self.items, list(PkAuto.objects.all().order_by("id")))

    def test_initial_bootstrap_audit_event_is_correct(self):
        self.assertEqual(0, self.init_bootsp.object_pk)
        self.assertTrue(self.init_bootsp.is_bootstrap)

    def test_initial_create_audit_event_is_correct(self):
        self.assertEqual(2, self.init_create.object_pk)
        self.assertTrue(self.init_create.is_create)

    def test_bootstrap_top_up_count(self):
        self.assertEqual(1, self.top_up_count)

    def test_bootstrap_top_up_event(self):
        pre_top_up_event_ids = [event.id for event in self.pre_top_up]
        top_up, = list(AuditEvent.objects.exclude(id__in=pre_top_up_event_ids))
        self.assertEqual(1, top_up.object_pk)
        self.assertTrue(top_up.is_bootstrap)

    def test_all_model_records_have_bootstrap_or_create_audit_events(self):
        self.assertEqual(
            self.items,
            list(PkAuto.objects.order_by("id").filter(pk__in=(
                AuditEvent.objects.cast_object_pks_list(PkAuto)
                .filter(models.Q(is_bootstrap=True) | models.Q(is_create=True))
            ))),
        )
