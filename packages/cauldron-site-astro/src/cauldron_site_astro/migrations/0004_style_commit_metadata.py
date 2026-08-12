from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_site_astro", "0003_sitechangeset_style_request_id"),
    ]

    operations = [
        # Make staged_theme_css nullable so None means "re-resolve at publish time"
        migrations.AlterField(
            model_name="sitechangeset",
            name="staged_theme_css",
            field=models.TextField(blank=True, null=True, default=None),
        ),
        # Style commit metadata for atomic pages CSS source write in publish()
        migrations.AddField(
            model_name="sitechangeset",
            name="style_scope",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="sitechangeset",
            name="style_target",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="sitechangeset",
            name="style_proposed_content",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="sitechangeset",
            name="style_base_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="sitechangeset",
            name="style_base_exists",
            field=models.BooleanField(default=False),
        ),
    ]
