from contextlib import contextmanager

from django.core.management.base import BaseCommand, CommandError

from field_audit.field_audit import get_audited_models
from field_audit.models import AuditEvent


class Command(BaseCommand):

    help = "Create bootstrap AuditEvent records for audited model classes."
    models = {}

    @classmethod
    def setup_models(cls):
        def model_name(model, unique=False):
            if unique:
                return f"{model._meta.app_label}.{model.__name__}"
            return model.__name__
        for model_class in get_audited_models():
            name = model_name(model_class)
            collision = cls.models.setdefault(name, model_class)
            if collision is not model_class:
                original = model_name(collision, unique=True)
                namesake = model_name(model_class, unique=True)
                if original == namesake:
                    raise InvalidModelState(
                        "Two audited models from the same app have the "
                        f"same name: {(collision, model_class)}"
                    )
                del cls.models[name]
                cls.models[original] = collision
                cls.models[namesake] = model_class

    def add_arguments(self, parser):
        parser.add_argument(
            "operation",
            choices=self.operations,
            help="Type of bootstrap operation to perform.",
        )
        parser.add_argument(
            "models",
            choices=sorted(self.models),
            nargs="+",
            help="Model class(es) to perform the bootstrap operation on.",
        )

    def handle(self, operation, models, **options):
        self.stdout.ending = None
        self.logfile = self.stdout
        for name in models:
            model_class = self.models[name]
            self.operations[operation](self, model_class)

    def init_all(self, model_class):
        query = model_class._default_manager.all()
        log_head = f"init: {model_class} ({query.count()}) ... "
        with self.bootstrap_action_log(log_head) as stream:
            count = self.do_bootstrap(
                model_class,
                AuditEvent.bootstrap_existing_model_records,
                iter_records=query.iterator,
            )
            stream.write(f"done ({count})")

    def top_up_missing(self, model_class):
        log_head = f"top-up: {model_class} ... "
        with self.bootstrap_action_log(log_head) as stream:
            count = self.do_bootstrap(model_class, AuditEvent.bootstrap_top_up)
            stream.write(f"done ({count})")

    def do_bootstrap(self, model_class, bootstrap_method, **bootstrap_kw):
        field_names = self.get_field_names(model_class)
        if not field_names:
            raise CommandError(
                f"invalid fields ({field_names!r}) for model: {model_class}"
            )
        return bootstrap_method(
            model_class,
            field_names,
            **bootstrap_kw,
        )

    operations = {
        "init": init_all,
        "top-up": top_up_missing,
    }

    @staticmethod
    def get_field_names(model_class):
        """Extract the field names from a model class.

        TODO: expose a method on the AuditEvent class for doing this.
        """
        return AuditEvent._field_names(model_class())

    @contextmanager
    def bootstrap_action_log(self, *args, **kw):
        end = kw.pop("end", "\n")
        self.log_info(*args, **kw, end="")
        yield self.logfile
        self.logfile.write(end)

    def log_info(self, *args, **kw):
        self._log("INFO", *args, **kw)

    def _log(self, level_name, message, *msg_args, end="\n"):
        print(f"{level_name}: {message % msg_args}", file=self.logfile, end=end)


Command.setup_models()


class InvalidModelState(CommandError):
    pass
