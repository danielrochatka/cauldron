"""Migration: add UIStyleChangeRequest and UIStyleAuditEvent tables."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0003_completion_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UIStyleChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)),
                ("status", models.CharField(
                    choices=[
                        ("proposed", "proposed"),
                        ("approved", "approved"),
                        ("applied", "applied"),
                        ("rejected", "rejected"),
                        ("conflicted", "conflicted"),
                    ],
                    db_index=True,
                    default="proposed",
                    max_length=32,
                )),
                ("scope", models.CharField(max_length=32)),
                ("target_path", models.CharField(max_length=512)),
                ("proposed_content", models.TextField()),
                ("base_hash", models.CharField(blank=True, default="", max_length=64)),
                ("proposed_hash", models.CharField(blank=True, default="", max_length=64)),
                ("description", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("error_summary", models.TextField(blank=True, default="")),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ui_style_requests_created",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ui_style_requests_reviewed",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("view_ui_styles", "Can view UI style overrides"),
                    ("propose_ui_style_changes", "Can propose UI style changes"),
                    ("approve_ui_style_changes", "Can approve UI style changes"),
                    ("view_ui_style_audit", "Can view UI style change audit"),
                ],
            },
        ),
        migrations.CreateModel(
            name="UIStyleAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)),
                ("sequence", models.PositiveIntegerField()),
                ("event_type", models.CharField(max_length=64)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("change_request", models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="audit_events",
                    to="cauldron_ai_admin.uistylechangerequest",
                )),
                ("actor", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="ui_style_audit_events",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["change_request", "sequence"],
            },
        ),
        migrations.AddIndex(
            model_name="uistylechangerequest",
            index=models.Index(fields=["status"], name="uiscr_status_idx"),
        ),
        migrations.AddIndex(
            model_name="uistylechangerequest",
            index=models.Index(fields=["scope"], name="uiscr_scope_idx"),
        ),
        migrations.AddIndex(
            model_name="uistylechangerequest",
            index=models.Index(fields=["created_at"], name="uiscr_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="uistyleauditevent",
            constraint=models.UniqueConstraint(
                fields=["change_request", "sequence"],
                name="uisae_unique_request_sequence",
            ),
        ),
    ]
