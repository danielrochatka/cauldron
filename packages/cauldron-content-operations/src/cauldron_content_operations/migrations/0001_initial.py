"""Initial migration for cauldron_content_operations."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _uuid_str():
    return str(uuid.uuid4())


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentPermissionProxy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ],
            options={
                "managed": False,
                "default_permissions": (),
                "permissions": [
                    ("view_published_content", "Can view published content"),
                    ("view_draft_content", "Can view draft content"),
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
        migrations.CreateModel(
            name="ContentChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.CharField(db_index=True, default=_uuid_str, max_length=64, unique=True)),
                ("workspace_changeset_id", models.CharField(db_index=True, max_length=64)),
                ("provider_name", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("lifecycle_state", models.CharField(
                    choices=[
                        ("proposed", "proposed"), ("validated", "validated"),
                        ("approved", "approved"), ("applying", "applying"),
                        ("applied", "applied"), ("rejected", "rejected"),
                        ("apply_failed", "apply_failed"), ("rolling_back", "rolling_back"),
                        ("rolled_back", "rolled_back"), ("rollback_failed", "rollback_failed"),
                        ("reconciliation_required", "reconciliation_required"),
                    ],
                    db_index=True, default="proposed", max_length=32,
                )),
                ("request_version", models.PositiveIntegerField(default=1)),
                ("payload_hash", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("validated_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("rolled_back_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, default="", max_length=64)),
                ("last_error_summary", models.TextField(blank=True, default="")),
                ("application_result_meta", models.JSONField(blank=True, default=dict)),
                ("reconciliation_meta", models.JSONField(blank=True, default=dict)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_approved", to=settings.AUTH_USER_MODEL)),
                ("applied_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_applied", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_created", to=settings.AUTH_USER_MODEL)),
                ("rejected_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_rejected", to=settings.AUTH_USER_MODEL)),
                ("rolled_back_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_rolled_back", to=settings.AUTH_USER_MODEL)),
                ("validated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_requests_validated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ContentAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(db_index=True, default=_uuid_str, max_length=64, unique=True)),
                ("sequence", models.PositiveIntegerField()),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("previous_state", models.CharField(blank=True, default="", max_length=32)),
                ("resulting_state", models.CharField(blank=True, default="", max_length=32)),
                ("provider", models.CharField(blank=True, default="", max_length=128)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("correlation_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_audit_events", to=settings.AUTH_USER_MODEL)),
                ("change_request", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="audit_events", to="cauldron_content_operations.contentchangerequest")),
            ],
            options={"ordering": ["change_request", "sequence"]},
        ),
        migrations.AddIndex(
            model_name="contentchangerequest",
            index=models.Index(fields=["lifecycle_state"], name="ccr_state_idx"),
        ),
        migrations.AddIndex(
            model_name="contentchangerequest",
            index=models.Index(fields=["provider_name"], name="ccr_provider_idx"),
        ),
        migrations.AddIndex(
            model_name="contentchangerequest",
            index=models.Index(fields=["idempotency_key"], name="ccr_idempotency_idx"),
        ),
        migrations.AddIndex(
            model_name="contentchangerequest",
            index=models.Index(fields=["created_at"], name="ccr_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="contentchangerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(idempotency_key__isnull=False) & ~models.Q(idempotency_key=""),
                fields=["idempotency_key"],
                name="ccr_unique_idempotency_key",
            ),
        ),
        migrations.AddIndex(
            model_name="contentauditevent",
            index=models.Index(fields=["change_request", "sequence"], name="cae_request_seq_idx"),
        ),
        migrations.AddIndex(
            model_name="contentauditevent",
            index=models.Index(fields=["event_type"], name="cae_event_type_idx"),
        ),
        migrations.AddIndex(
            model_name="contentauditevent",
            index=models.Index(fields=["occurred_at"], name="cae_occurred_idx"),
        ),
        migrations.AddConstraint(
            model_name="contentauditevent",
            constraint=models.UniqueConstraint(
                fields=["change_request", "sequence"],
                name="cae_unique_request_sequence",
            ),
        ),
    ]
