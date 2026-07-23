"""Add view_content_change_requests permission and scope idempotency to creator."""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_content_operations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="contentpermissionproxy",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": [
                    ("view_published_content", "Can view published content"),
                    ("view_draft_content", "Can view draft content"),
                    ("view_content_change_requests", "Can view content change requests"),
                    ("propose_content_changes", "Can propose content changes"),
                    ("validate_content_changes", "Can validate content changes"),
                    ("approve_content_changes", "Can approve content changes"),
                    ("reject_content_changes", "Can reject content changes"),
                    ("apply_content_changes", "Can apply content changes"),
                    ("rollback_content_changes", "Can roll back content changes"),
                    ("view_content_audit", "Can view content audit history"),
                ],
            },
        ),
        migrations.RemoveConstraint(
            model_name="contentchangerequest",
            name="ccr_unique_idempotency_key",
        ),
        migrations.AddConstraint(
            model_name="contentchangerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(idempotency_key__gt=""),
                fields=["created_by", "idempotency_key"],
                name="ccr_unique_creator_idempotency_key",
            ),
        ),
    ]
