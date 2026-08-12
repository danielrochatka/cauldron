from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_ai_admin", "0011_adminairun_prompt_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="uistylechangerequest",
            name="site_changeset_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=36),
        ),
    ]
