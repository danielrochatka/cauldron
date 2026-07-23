"""Add generic metadata JSON field and rollback_artifact_digest to ContentChangeRequest."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_content_operations", "0003_alter_contentpermissionproxy_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="contentchangerequest",
            name="metadata",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="contentchangerequest",
            name="rollback_artifact_digest",
            field=models.CharField(max_length=64, blank=True, default=""),
        ),
    ]
