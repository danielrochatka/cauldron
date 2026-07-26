from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0010_adminairun_model_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminairun",
            name="prompt_global_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="adminairun",
            name="prompt_tool_versions",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="adminairun",
            name="prompt_included_tools",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
