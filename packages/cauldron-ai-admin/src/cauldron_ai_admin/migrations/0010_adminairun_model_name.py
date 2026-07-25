from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_ai_admin", "0009_manage_admin_ai_settings_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminairun",
            name="model_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
