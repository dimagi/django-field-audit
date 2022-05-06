# Audit Field Changes on Django Models

[![tests][tests_badge]][tests_link]
![coverage][coverage_badge]
[![pypi package][pypi_badge]][pypi_link]

[tests_badge]: https://github.com/dimagi/django-field-audit/actions/workflows/tests.yml/badge.svg
[tests_link]: https://github.com/dimagi/django-field-audit/actions/workflows/tests.yml
[coverage_badge]: https://github.com/dimagi/django-field-audit/raw/coverage-badge/coverage.svg
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

Add the app to your Django `INSTALLED_APPS` configuration to enable the app and
run migrations. Installed apps settings example:

```python
INSTALLED_APPS = [
    # ...
    "field_audit",
]
```

If `changed_by` auditing is desired (enabled by default), add the app middleware
to your Django `MIDDLEWARE` configuration. For example:

```python
MIDDLEWARE = [
    # ...
    "field_audit.middleware.FieldAuditMiddleware",
]
```

#### Custom settings

| Name                              | Description                                                | Default value when unset
|:----------------------------------|:-----------------------------------------------------------|:------------------------
| `FIELD_AUDIT_AUDITEVENT_MANAGER`  | A custom manager to use for the `AuditEvent` Model.        | `field_audit.models.DefaultAuditEventManager`
| `FIELD_AUDIT_FIELDCHANGE_MANAGER` | A custom manager to use for the `FieldChange` Model.       | `django.db.models.Manager`
| `FIELD_AUDIT_AUDITORS`            | A custom list of auditors for acquiring `changed_by` info. | `["field_audit.auditors.RequestAuditor", "field_audit.auditors.SystemUserAuditor"]`

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
- Write full library documentation using github.io.
- Switch to `pytest` to support Python 3.10.
- Write `test_library.py` functional test module for entire library.
- Add support for `QuerySet` write operations (`update()`, etc).
- Possibly remove `AutoField` accommodations and just force `BigAutoField`.

### Backlog

- Don't use `threading.local()` for tracking the request.
- Write model path change migration utility [maybe].
- Add to optimization for `instance.save(save_fields=[...])` [maybe].
- Support adding new audit fields on the same model at different times (instead
  of raising `AlreadyAudited`) [maybe].
