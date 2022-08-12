from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('field_audit', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditevent',
            name='is_bootstrap',
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name='auditevent',
            constraint=models.CheckConstraint(
                name='field_audit_auditevent_chk_create_or_delete_or_bootstrap',
                check=models.Q(
                    models.Q(
                        models.Q(('is_create', True), ('is_delete', True)),
                        models.Q(('is_bootstrap', True), ('is_create', True)),
                        models.Q(('is_bootstrap', True), ('is_delete', True)),
                        _connector='OR',
                    ),
                    _negated=True,
                ),
            ),
        ),
        migrations.RemoveConstraint(
            model_name='auditevent',
            name='field_audit_auditevent_valid_create_or_delete',
        ),
    ]
