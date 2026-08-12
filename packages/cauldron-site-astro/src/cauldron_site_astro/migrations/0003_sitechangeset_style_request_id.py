from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_site_astro", "0002_sitechangeset_affected_item_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitechangeset",
            name="style_request_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=36),
        ),
    ]
