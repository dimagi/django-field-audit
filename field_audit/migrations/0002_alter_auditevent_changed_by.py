from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('field_audit', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditevent',
            name='changed_by',
            field=models.JSONField(default={}),
            preserve_default=False,
        ),
    ]
