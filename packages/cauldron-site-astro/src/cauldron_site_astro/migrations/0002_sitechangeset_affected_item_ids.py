from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_site_astro", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitechangeset",
            name="affected_item_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
