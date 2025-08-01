# Django Field Audit change log

## v1.4.0 - 2025-07-29
- Extract audit logic to the `AuditService` class and allow overriding the class (using the `FIELD_AUDIT_SERVICE_CLASS` setting) to provide custom logic.
- Update project to use `uv` for dependency management and `ruff` for linting.

## v1.3.0 - 2025-07-01
- Support auditing of many-to-many relationships
- Use `DjangoJSONEncoder` for serializing change deltas.

## v1.2.9 - 2025-02-21
- Don't fail when attempting to use the `who` command to get the current user if the `who` command is not available e.g. on Windows.

## v1.2.8 - 2024-01-24
- Use Django's `timezone.now` instead of `datetime.now` to ensure the correct
  timezone is used when creating audit events.

## v1.2.7 - 2024-01-18
- Resolve a recursion issue with multiple deferred audit fields.

## v1.2.6 - 2023-03-03
- Resolve bug with incorrect previous values when updating an audited field in
  the save method of the object.

## v1.2.5 - 2023-02-22
- All audited methods use SQL transactions to ensure changes to audited models
  are only written to the database if the audit event is successfully written
- Optimize `QuerySet.delete` to only fetch audited values from database
- Add support for auditing `QuerySet.bulk_create` method

## v1.2.4 - 2022-11-22
- Add support for auditing `QuerySet.update` method

## v1.2.3 - 2022-09-02

- Use batching by default for all bootstrap operations
- Add a `--batch-size` option to the bootstrap management command

## v1.2.2 - 2022-08-19

- Add management command for bootstrapping
- Add new "top up" bootstrap action

## v1.2.1 - 2022-08-12

- Fix migration constraint condition ordering which caused Django<4.0 to think
  a new migration was needed.

## v1.2.0 - 2022-08-05

- Add ability to bootstrap audit events for existing model records.

## v1.1.0 - 2022-07-22

- Add Django compatibility tests.
- Add auditing support for "special" `QuerySet` write methods

## v1.0.0 - 2022-05-31

- Remove `FieldChange` model in favor of storing the full delta on the
  `AuditEvent` model.
- Add ability to define custom class paths for audited models.
- **IMPORTANT NOTE**: if you used version 0.2 to generate audit records, this
  upgrade is destructive.

  Version 0.2 was never used for production by the author, and migrating data
  from that version to 1.0.0 is not implemented. If you wish to retain existing
  (version 0.2) audit records, you will need to preserve those records and
  migrate them yourself.

  ### Upgrade from 0.2 (destroying existing data)

  Steps to upgrade if you do _not_ wish to retain any existing data are as
  follows:

  ```shell
  python manage.py migrate field_audit zero
  pip install -U django-field-audit
  python manage.py migrate field_audit
  ```

  ### Upgrade from 0.2 (retaining existing data)

  Steps to upgrade while retaining your existing data are as follows:

  1. Run the following commands against your database to preserve the existing
     `field_audit_auditevent` table:

     ```sql
     ALTER TABLE field_audit_auditevent RENAME TO field_audit_v02_auditevent;
     ALTER SEQUENCE field_audit_auditevent_id_seq RENAME TO field_audit_v02_auditevent_id_seq;
     ALTER INDEX field_audit_auditevent_pkey RENAME TO field_audit_v02_auditevent_pkey;
     ALTER INDEX field_audit_auditevent_event_date_129a0da2 RENAME TO field_audit_v02_auditevent_event_date_129a0da2;
     ALTER INDEX field_audit_auditevent_object_class_path_034c1566 RENAME TO field_audit_v02_auditevent_object_class_path_034c1566;
     ALTER INDEX field_audit_auditevent_object_class_path_034c1566_like RENAME TO field_audit_v02_auditevent_object_class_path_034c1566_like;
     ```

  2. Fake the zero migration for the `field_audit` app:

     ```shell
     python manage.py migrate field_audit zero --fake
     ```

  3. Upgrade and run migrations:

     ```shell
     pip install -U django-field-audit
     python manage.py migrate field_audit
     ```

  4. Migrate your data from the old tables (`field_audit_v02_auditevent` and
     `field_audit_fieldchange`) into the new `field_audit.models.AuditEvent`
     model.


## v0.2 - 2022-05-24

- Initial implementation.
- There is no version `0.1`.
