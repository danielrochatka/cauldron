"""Initial migration for cauldron_ai_admin."""
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
            name="AdminAIRun",
            fields=[
                ("run_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(
                    choices=[
                        ("created", "created"),
                        ("running", "running"),
                        ("waiting_for_approval", "waiting_for_approval"),
                        ("completed", "completed"),
                        ("failed", "failed"),
                        ("cancelled", "cancelled"),
                    ],
                    db_index=True,
                    default="created",
                    max_length=32,
                )),
                ("provider_name", models.CharField(max_length=128)),
                ("provider_request_id", models.CharField(blank=True, default="", max_length=256)),
                ("user_request", models.TextField()),
                ("final_response", models.TextField(blank=True, default="")),
                ("correlation_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("tool_call_count", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, default="", max_length=128)),
                ("error_summary", models.CharField(blank=True, default="", max_length=512)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("actor", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("use_admin_ai", "Can invoke the Admin AI assistant"),
                    ("view_admin_ai_runs", "Can view Admin AI run history"),
                    ("view_admin_ai_audit", "Can view Admin AI audit records"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AdminAIToolInvocation",
            fields=[
                ("invocation_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tool_call_id", models.CharField(blank=True, default="", max_length=128)),
                ("tool_name", models.CharField(db_index=True, max_length=128)),
                ("tool_version", models.CharField(blank=True, default="", max_length=32)),
                ("owning_module", models.CharField(blank=True, default="", max_length=128)),
                ("risk_level", models.CharField(
                    choices=[
                        ("READ_ONLY", "READ_ONLY"),
                        ("PROPOSE", "PROPOSE"),
                        ("MAINTENANCE", "MAINTENANCE"),
                        ("PRIVILEGED", "PRIVILEGED"),
                    ],
                    db_index=True,
                    max_length=32,
                )),
                ("status", models.CharField(
                    choices=[
                        ("requested", "requested"),
                        ("authorized", "authorized"),
                        ("running", "running"),
                        ("completed", "completed"),
                        ("denied", "denied"),
                        ("failed", "failed"),
                        ("timed_out", "timed_out"),
                    ],
                    db_index=True,
                    max_length=32,
                )),
                ("arguments_hash", models.CharField(blank=True, default="", max_length=64)),
                ("argument_summary", models.CharField(blank=True, default="", max_length=512)),
                ("result_summary", models.CharField(blank=True, default="", max_length=1024)),
                ("error_code", models.CharField(blank=True, default="", max_length=128)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("run", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="invocations",
                    to="cauldron_ai_admin.adminairun",
                )),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AddIndex(
            model_name="adminairun",
            index=models.Index(fields=["status"], name="aair_status_idx"),
        ),
        migrations.AddIndex(
            model_name="adminairun",
            index=models.Index(fields=["correlation_id"], name="aair_corr_idx"),
        ),
        migrations.AddIndex(
            model_name="adminairun",
            index=models.Index(fields=["created_at"], name="aair_created_idx"),
        ),
        migrations.AddIndex(
            model_name="adminaitoolinvocation",
            index=models.Index(fields=["tool_name"], name="aai_ti_toolname_idx"),
        ),
        migrations.AddIndex(
            model_name="adminaitoolinvocation",
            index=models.Index(fields=["status"], name="aai_ti_status_idx"),
        ),
        migrations.AddIndex(
            model_name="adminaitoolinvocation",
            index=models.Index(fields=["risk_level"], name="aai_ti_risk_idx"),
        ),
    ]
