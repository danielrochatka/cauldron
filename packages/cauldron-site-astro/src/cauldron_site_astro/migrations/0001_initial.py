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
            name="SiteChangeSet",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("preparing", "Preparing"),
                            ("draft_ready", "Draft Ready"),
                            ("preview_failed", "Preview Failed"),
                            ("publishing", "Publishing"),
                            ("published", "Published"),
                            ("publish_failed", "Publish Failed"),
                        ],
                        db_index=True,
                        default="preparing",
                        max_length=32,
                    ),
                ),
                ("content_request_ids", models.JSONField(blank=True, default=list)),
                ("staged_theme_css", models.TextField(blank=True)),
                (
                    "originating_run_id",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                ("preview_dir", models.CharField(blank=True, max_length=512)),
                ("page_routes", models.JSONField(blank=True, default=list)),
                ("publish_build_result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("draft_ready_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                (
                    "creator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="site_change_sets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Site Change Set",
                "verbose_name_plural": "Site Change Sets",
                "ordering": ["-created_at"],
            },
        ),
    ]
