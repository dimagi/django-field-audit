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

1. Enable the feature by calling the audit decorator specifying
   `@audit_fields(..., audit_special_queryset_writes=True)`.
2. Configure the model class so its default manager is an instance of
   `field_audit.models.AuditingManager`.
3. All calls to the four "special" QuerySet write methods require an extra
   `audit_action` keyword argument whose value is one of:
   - `field_audit.models.AuditAction.AUDIT`
   - `field_audit.models.AuditAction.IGNORE`

**Important Note**: at this time, only the `QuerySet.delete()` "special" write
method can actually perform change auditing when called with
`audit_action=AuditAction.AUDIT`. The other three methods are currently not
implemented and will raise `NotImplementedError` if called with that action.
Implementing these remaining methods remains a task for the future, see **TODO**
below. All four methods do support `audit_action=AuditAction.IGNORE` usage,
however.


### Using with SQLite

This app uses Django's `JSONField` which means if you intend to use the app with
a SQLite database, the SQLite `JSON1` extension is required. If your system's
Python `sqlite3` library doesn't ship with this extension enabled, see
[this article](https://code.djangoproject.com/wiki/JSON1Extension) for details
on how to enable it.


## Contributing

All feature and bug contributions are expected to be covered by tests.

### Setup for developers

Create/activate a python virtualenv and install the required dependencies.

```shell
cd django-field-audit
mkvirtualenv django-field-audit  # or however you choose to setup your environment
pip install django nose flake8 coverage
```

### Running tests

**Note**: By default, local tests use an in-memory SQLite database. Ensure that
your local Python's `sqlite3` library ships with the `JSON1` extension enabled
(see [Using with SQLite](#using-with-sqlite)).

- Tests
  ```shell
  nosetests
  ```

- Style check
  ```shell
  flake8 --config=setup.cfg
  ```

- Coverage
  ```shell
  coverage run -m nose
  coverage report -m
  ```

### Adding migrations

The example `manage.py` is available for making new migrations.

```shell
python example/manage.py makemigrations field_audit
```

### Uploading to PyPI

Package and upload the generated files.

```shell
pip install -r pkg-requires.txt

python setup.py sdist bdist_wheel
twine upload dist/*
```

## TODO

- Write backfill migration utility / management command.
- Implement auditing for the remaining "special" QuerySet write operations:
  - `bulk_create()`
  - `bulk_update()`
  - `update()`
- Write full library documentation using github.io.
- Switch to `pytest` to support Python 3.10.

### Backlog

- Add to optimization for `instance.save(save_fields=[...])` [maybe].
- Support adding new audit fields on the same model at different times (instead
  of raising `AlreadyAudited`) [maybe].
