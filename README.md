# Audit Field Changes on Django Models

[![tests][tests_badge]][tests_link]
[![coverage][coverage_badge]][coverage_link]
[![pypi package][pypi_badge]][pypi_link]

[tests_badge]: https://github.com/dimagi/django-field-audit/actions/workflows/tests.yml/badge.svg
[tests_link]: https://github.com/dimagi/django-field-audit/actions/workflows/tests.yml
[coverage_badge]: https://github.com/dimagi/django-field-audit/raw/coverage-badge/coverage.svg
[coverage_link]: https://github.com/dimagi/django-field-audit/actions/workflows/coverage.yml
[pypi_badge]: https://badge.fury.io/py/django-field-audit.svg
[pypi_link]: https://pypi.org/project/django-field-audit/

A Django app for auditing field changes on database models.

## Installation
```
pip install django-field-audit
```

## Documentation

<!--
The [django-field-audit documentation][docs] shows how to use this library to
audit field changes on Django Models.

[docs]: https://dimagi.github.io/django-field-audit/
-->

### Django Settings

To enable the app, add it to your Django `INSTALLED_APPS` configuration and run
migrations. Settings example:

```python
INSTALLED_APPS = [
    # ...
    "field_audit",
]
```

The "auditor chain" (see `FIELD_AUDIT_AUDITORS` in the **Custom settings** table
below) is configured out of the box with the default auditors. If
`change_context` auditing is desired for authenticated Django requests, add the
app middleware to your Django `MIDDLEWARE` configuration. For example:

```python
MIDDLEWARE = [
    # ...
    "field_audit.middleware.FieldAuditMiddleware",
]
```

The audit chain can be updated to use custom auditors (subclasses of
`field_audit.auditors.BaseAuditor`). If `change_context` auditing is not
desired, the audit chain can be cleared to avoid extra processing:

```python
FIELD_AUDIT_AUDITORS = []
```

#### Custom settings details

| Name                              | Description                                                    | Default value when unset
|:----------------------------------|:---------------------------------------------------------------|:------------------------
| `FIELD_AUDIT_AUDITEVENT_MANAGER`  | A custom manager to use for the `AuditEvent` Model.            | `field_audit.models.DefaultAuditEventManager`
| `FIELD_AUDIT_AUDITORS`            | A custom list of auditors for acquiring `change_context` info. | `["field_audit.auditors.RequestAuditor", "field_audit.auditors.SystemUserAuditor"]`
| `FIELD_AUDIT_SERVICE_CLASS`       | A custom service class for audit logic implementation.         | `field_audit.services.AuditService`

### Custom Audit Service

The audit logic has been extracted into a separate `AuditService` class to improve separation of concerns and enable easier customization of audit behavior. Users can provide custom audit implementations by subclassing `AuditService` and configuring the `FIELD_AUDIT_SERVICE_CLASS` setting.

#### Creating a Custom Audit Service

```python
# myapp/audit.py

from field_audit import AuditService

class CustomAuditService(AuditService):
    def get_field_value(self, instance, field_name, bootstrap=False):
        # Custom logic for extracting field values
        value = super().get_field_value(instance, field_name, bootstrap)
        
        # Example: custom serialization or transformation
        if field_name == 'sensitive_field':
            value = '[REDACTED]'
            
        return value
```

Then configure it in your Django settings:

```python
# settings.py

FIELD_AUDIT_SERVICE_CLASS = 'myapp.audit.CustomAuditService'
```

#### Backward Compatibility

The original `AuditEvent` class methods are maintained for backward compatibility but are now deprecated in favor of the service-based approach. These methods will issue deprecation warnings and delegate to the configured audit service.

### Model Auditing

To begin auditing Django models, import the `field_audit.audit_fields` decorator
and decorate models specifying which fields should be audited for changes.
Example code:

```python
# flight/models.py

from django.db import models
from field_audit import audit_fields


@audit_fields("tail_number", "make_model", "operated_by")
class Aircraft(models.Model):
    id = AutoField(primary_key=True)
    tail_number = models.CharField(max_length=32, unique=True)
    make_model = models.CharField(max_length=64)
    operated_by = models.CharField(max_length=64)
```

#### Audited DB write operations

By default, Model and QuerySet methods are audited, with the exception of four
"special" QuerySet methods:

| DB Write Method               | Audited
|:------------------------------|:-------
| `Model.delete()`              | Yes
| `Model.save()`                | Yes
| `QuerySet.bulk_create()`      | No
| `QuerySet.bulk_update()`      | No
| `QuerySet.create()`           | Yes (via `Model.save()`)
| `QuerySet.delete()`           | No
| `QuerySet.get_or_create()`    | Yes (via `QuerySet.create()`)
| `QuerySet.update()`           | No
| `QuerySet.update_or_create()` | Yes (via `QuerySet.get_or_create()` and `Model.save()`)

#### Auditing Special QuerySet Writes

Auditing for the four "special" QuerySet methods that perform DB writes (labeled
**No** in the table above) _can_ be enabled. This requires three extra usage
details:

> **Warning**
> Enabling auditing on these QuerySet methods might have significant
> performance implications, especially on large datasets, since audit events are
> constructed in memory and bulk written to the database.

1. Enable the feature by calling the audit decorator specifying
   `@audit_fields(..., audit_special_queryset_writes=True)`.
2. Configure the model class so its default manager is an instance of
   `field_audit.models.AuditingManager`.
3. All calls to the four "special" QuerySet write methods require an extra
   `audit_action` keyword argument whose value is one of:
   - `field_audit.models.AuditAction.AUDIT`
   - `field_audit.models.AuditAction.IGNORE`

##### Important Notes

- Specifying `audit_special_queryset_writes=True` (step **1** above) without
  setting the default manager to an instance of `AuditingManager` (step **2**
  above) will raise an exception when the model class is evaluated.
- At this time, `QuerySet.delete()`, `QuerySet.update()`,
  and `QuerySet.bulk_create()` "special" write methods can actually perform
  change auditing when called with `audit_action=AuditAction.AUDIT`. 
  `QuerySet.bulk_update()` is not currently implemented and will raise
  `NotImplementedError` if called with that action. Implementing this remaining
  method remains a task for the future, see **TODO** below. All four methods do
  support `audit_action=AuditAction.IGNORE` usage, however.
- All audited methods use transactions to ensure changes to audited models
  are only committed to the database if audit events are successfully created
  and saved as well.

### Auditing Many-to-Many fields

Many-to-Many field changes are automatically audited through Django signals when
included in the `@audit_fields` decorator. Changes to M2M relationships generate
audit events immediately without requiring `save()` calls.

```python
# Example model with audited M2M field
@audit_fields("name", "title", "certifications")
class CrewMember(models.Model):
    name = models.CharField(max_length=256)
    title = models.CharField(max_length=64)
    certifications = models.ManyToManyField('Certification', blank=True)
```

#### Supported M2M operations

All standard M2M operations create audit events:

```python
crew_member = CrewMember.objects.create(name='Test Pilot', title='Captain')
cert1 = Certification.objects.create(name='PPL', certification_type='Private')

crew_member.certifications.add(cert1)         # Creates audit event
crew_member.certifications.remove(cert1)      # Creates audit event
crew_member.certifications.set([cert1])       # Creates audit event
crew_member.certifications.clear()            # Creates audit event
```

#### M2M audit event structure

M2M changes use specific delta structures in audit events:

- **Add**: `{'certifications': {'add': [1, 2]}}`
- **Remove**: `{'certifications': {'remove': [2]}}`
- **Clear**: `{'certifications': {'remove': [1, 2]}}`
- **Create** / **Bootstrap**: `{'certifications': {'new': []}}`

#### Bootstrap events for models with existing records

In the scenario where auditing is enabled for a model with existing data, it can
be valuable to generate "bootstrap" audit events for all of the existing model
records in order to ensure that there is at least one audit event record for
every model instance that currently exists.  There is a migration utility for
performing this bootstrap operation. Example code:

```python
# flight/migrations/0002_bootstrap_aircarft_auditing.py

from django.db import migrations, models
from field_audit.utils import run_bootstrap

from flight.models import Aircraft


class Migration(migrations.Migration):

    dependencies = [
        ('flight', '0001_initial'),
    ]

    operations = [
        run_bootstrap(Aircraft, ["tail_number", "make_model", "operated_by"])
    ]
```

##### Bootstrap events via management command

If bootstrapping is not suitable during migrations, there is a management command for
performing the same operation.  The management command does not accept arbitrary
field names for bootstrap records, and uses the fields configured by the
existing `audit_fields(...)` decorator on the model. Example (analogous to
migration action shown above):

```sh
manage.py bootstrap_field_audit_events init Aircraft
```

Additionally, if a post-migration bootstrap "top up" action is needed, the
the management command can also perform this action. A "top up" operation
creates bootstrap audit events for any existing model records which do not have
a "create" or "bootstrap" `AuditEvent` record. Note that the management command
is currently the only way to "top up" bootstrap audit events. Example:

```sh
manage.py bootstrap_field_audit_events top-up Aircraft
```

### Using with SQLite

This app uses Django's `JSONField` which means if you intend to use the app with
a SQLite database, the SQLite `JSON1` extension is required. If your system's
Python `sqlite3` library doesn't ship with this extension enabled, see
[this article](https://code.djangoproject.com/wiki/JSON1Extension) for details
on how to enable it.


## Contributing

All feature and bug contributions are expected to be covered by tests.

### Setup for developers

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Install uv and then install the project dependencies:

```shell
cd django-field-audit
uv sync
```

### Running tests

**Note**: By default, local tests use an in-memory SQLite database. Ensure that
your local Python's `sqlite3` library ships with the `JSON1` extension enabled
(see [Using with SQLite](#using-with-sqlite)).

- Tests
  ```shell
  uv run pytest
  ```

- Style check
  ```shell
  ruff check
  ```

- Coverage
  ```shell
  uv run coverage run -m pytest
  uv run coverage report -m
  ```

### Adding migrations

The example `manage.py` is available for making new migrations.

```shell
uv run python example/manage.py makemigrations field_audit
```

### Publishing a new version to PyPI

Push a new tag to Github using the format vX.Y.Z where X.Y.Z matches the version
in [`__init__.py`](field_audit/__init__.py). Also ensure that the changelog is up to date.

Publishing is automated with [Github Actions](.github/workflows/pypi.yml).

## TODO

- Implement auditing for the remaining "special" QuerySet write operations:
  - `bulk_update()`
- Write full library documentation using github.io.

### Backlog

- Add to optimization for `instance.save(save_fields=[...])` [maybe].
- Support adding new audit fields on the same model at different times (instead
  of raising `AlreadyAudited`) [maybe].
