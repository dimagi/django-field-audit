import django.db.models.deletion
from django.db import migrations, models

from ..apps import FieldAuditConfig
from ..models import get_date
AutoFieldClass = FieldAuditConfig.get_auto_field()


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='AuditEvent',
            fields=[
                ('id', AutoFieldClass(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),  # noqa: E501
                ('event_date', models.DateTimeField(db_index=True, default=get_date)),  # noqa: E501
                ('object_class_path', models.CharField(db_index=True, max_length=255)),  # noqa: E501
                ('object_pk', models.JSONField()),
                ('changed_by', models.JSONField(null=True)),
                ('is_create', models.BooleanField(default=False)),
                ('is_delete', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='FieldChange',
            fields=[
                ('id', AutoFieldClass(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),  # noqa: E501
                ('field_name', models.CharField(db_index=True, max_length=127)),
                ('delta', models.JSONField()),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='changes', to='field_audit.auditevent')),  # noqa: E501
            ],
            options={
                'unique_together': {('event', 'field_name')},
            },
        ),
        migrations.AddConstraint(
            model_name='auditevent',
            constraint=models.CheckConstraint(
                name='field_audit_auditevent_valid_create_or_delete',
                check=models.Q(
                    ('is_create', True),
                    ('is_delete', True),
                    _negated=True,
                ),
            ),
        ),
    ]
