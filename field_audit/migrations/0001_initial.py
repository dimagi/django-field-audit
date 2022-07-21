from django.db import migrations, models

from ..models import get_date


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='AuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),  # noqa: E501
                ('event_date', models.DateTimeField(db_index=True, default=get_date)),  # noqa: E501
                ('object_class_path', models.CharField(db_index=True, max_length=255)),  # noqa: E501
                ('object_pk', models.JSONField()),
                ('change_context', models.JSONField()),
                ('is_create', models.BooleanField(default=False)),
                ('is_delete', models.BooleanField(default=False)),
                ('delta', models.JSONField()),
            ],
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
