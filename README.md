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

### Using with SQLite

This app uses Django's `JSONField` which means if you intend to use the app with
a SQLite database, the SQLite `JSON1` extension is required. If your system's
Python `sqlite3` library doesn't ship with this extension enabled, see
[this article]((https://code.djangoproject.com/wiki/JSON1Extension)) for details
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
- Add support for `QuerySet` write operations (`update()`, etc).
- Write full library documentation using github.io.
- Switch to `pytest` to support Python 3.10.
- Write `test_library.py` functional test module for entire library.

### Backlog

- Add to optimization for `instance.save(save_fields=[...])` [maybe].
- Support adding new audit fields on the same model at different times (instead
  of raising `AlreadyAudited`) [maybe].
