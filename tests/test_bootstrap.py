from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import models
from django.test import TestCase


from field_audit.field_audit import get_audited_models
from field_audit.management.commands import bootstrap
from field_audit.models import AuditEvent
from tests.models import PkAuto


@contextmanager
def restore_command_models(command_class):
    backup = command_class.models
    yield
    command_class.models = backup


class TestCommand(TestCase):

    def setUp(self):
        super().setUp()

        # management command stdout sent here, set to None make noisy tests
        self.quiet = StringIO()

        # create some records for bootstrapping
        for _ in range(2):
            PkAuto.objects.create()
        # clear the "create" audit events
        AuditEvent.objects.all().delete()

    def test_setup_models_populates_models_dict(self):
        with restore_command_models(bootstrap.Command):
            bootstrap.Command.models = {}
            bootstrap.Command.setup_models()
            self.assertEqual(
                set(get_audited_models()),
                set(bootstrap.Command.models.values()),
            )

    def test_setup_models_uses_model_name(self):
        with restore_command_models(bootstrap.Command):
            bootstrap.Command.models = {}
            bootstrap.Command.setup_models()
            self.assertIs(PkAuto, bootstrap.Command.models["PkAuto"])

    def test_setup_models_uses_app_name_to_prevent_model_name_collisions(self):
        with restore_command_models(bootstrap.Command):
            class ModelX(models.Model):
                pass
            bootstrap.Command.models = {"PkAuto": ModelX}
            patch_kw = {"return_value": {PkAuto: "cls"}}
            with patch.object(bootstrap, "get_audited_models", **patch_kw):
                bootstrap.Command.setup_models()
            self.assertEqual(
                {"tests.ModelX": ModelX, "tests.PkAuto": PkAuto},
                bootstrap.Command.models,
            )

    def test_setup_models_crashes_on_verbose_model_name_collision(self):

        class ModelY(models.Model):
            pass

        class ModelZ(models.Model):
            pass

        ModelZ.__name__ = ModelY.__name__
        with restore_command_models(bootstrap.Command):
            patch_kw = {"return_value": {ModelY: "y", ModelZ: "z"}}
            with (
                patch.object(bootstrap, "get_audited_models", **patch_kw),
                self.assertRaises(bootstrap.InvalidModelState),
            ):
                bootstrap.Command.setup_models()

    def test_bootstrap_crashes_early_if_model_has_invalid_fields(self):
        with (
            patch.object(PkAuto, AuditEvent.ATTACH_FIELD_NAMES_AT, []),
            self.assertRaises(CommandError),
        ):
            call_command(
                "bootstrap", "init", "PkAuto", "--commit", stdout=self.quiet,
            )

    def test_bootstrap_database_writes_require_commit_option(self):
        self.assertEqual([], list(AuditEvent.objects.all()))
        call_command(
            "bootstrap", "init", "PkAuto", "--commit", stdout=self.quiet,
        )
        self.assertEqual(
            set(PkAuto.objects.all().values_list("pk", flat=True)),
            set(AuditEvent.objects.all().values_list("object_pk", flat=True)),
        )

    def test_bootstrap_omitting_commit_option_writes_nothing(self):
        self.assertEqual([], list(AuditEvent.objects.all()))
        call_command("bootstrap", "init", "PkAuto", stdout=self.quiet)
        self.assertEqual([], list(AuditEvent.objects.all()))

    def test_bootstrap_init_creates_audit_events_for_all_model_records(self):
        call_command(
            "bootstrap", "init", "PkAuto", "--commit", stdout=self.quiet
        )
        self.assertEqual(
            set(PkAuto.objects.all().values_list("pk", flat=True)),
            set(
                AuditEvent.objects.by_model(PkAuto)
                .filter(is_bootstrap=True)
                .values_list("object_pk", flat=True)
            ),
        )

    def test_bootstrap_top_up_creates_audit_events_for_some_model_records(self):
        need_bootstrap = set(PkAuto.objects.all().values_list("pk", flat=True))
        # generate two more models (generates two "create" audit events)
        for _ in range(2):
            PkAuto.objects.create()
        # convert one of them to a bootstrap event
        event = AuditEvent.objects.first()
        event.is_create = False
        event.is_bootstrap = True
        event.save()
        pre_top_up_event_ids = set(
            AuditEvent.objects.all().values_list("id", flat=True)
        )
        call_command(
            "bootstrap", "top-up", "PkAuto", "--commit", stdout=self.quiet
        )
        self.assertEqual(
            need_bootstrap,
            set(
                AuditEvent.objects.exclude(id__in=pre_top_up_event_ids)
                .values_list("object_pk", flat=True)
            ),
        )
