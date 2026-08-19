"""Initial migration for cauldron_ai_attachments."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AttachmentRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_attachments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=128)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("checksum_sha256", models.CharField(max_length=64)),
                (
                    "extraction_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("extracted", "Extracted"),
                            ("failed", "Failed"),
                            ("unsupported", "Unsupported format"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("extracted_text", models.TextField(blank=True)),
                ("extraction_error", models.TextField(blank=True)),
                ("word_count", models.PositiveIntegerField(default=0)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("section_headings", models.JSONField(default=list)),
                ("truncated", models.BooleanField(default=False)),
                ("extractor_name", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "app_label": "cauldron_ai_attachments",
                "ordering": ["-created_at"],
                "permissions": [
                    ("upload_attachment", "Can upload Admin AI attachments"),
                    ("read_attachment", "Can read Admin AI attachment content"),
                ],
            },
        ),
    ]
