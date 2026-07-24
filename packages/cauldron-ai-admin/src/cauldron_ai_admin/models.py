"""Durable audit records for Admin AI runs and tool invocations."""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


RUN_STATUS_CHOICES = [
    ("created", "created"),
    ("running", "running"),
    ("waiting_for_approval", "waiting_for_approval"),
    ("completed", "completed"),
    ("failed", "failed"),
    ("cancelled", "cancelled"),
]

INVOCATION_STATUS_CHOICES = [
    ("requested", "requested"),
    ("authorized", "authorized"),
    ("running", "running"),
    ("completed", "completed"),
    ("denied", "denied"),
    ("failed", "failed"),
    ("timed_out", "timed_out"),
]

RISK_LEVEL_CHOICES = [
    ("READ_ONLY", "READ_ONLY"),
    ("PROPOSE", "PROPOSE"),
    ("MAINTENANCE", "MAINTENANCE"),
    ("PRIVILEGED", "PRIVILEGED"),
]

_RUN_STATUS_VALUES = [v for v, _ in RUN_STATUS_CHOICES]
_INVOCATION_STATUS_VALUES = [v for v, _ in INVOCATION_STATUS_CHOICES]


class ConcurrentModificationError(RuntimeError):
    """Raised when an optimistic-concurrency finalize fails because the row
    was updated under our feet."""


class AdminAIRun(models.Model):
    """One end-to-end natural-language admin request.

    Rows are created immediately on request, and updated as the run
    progresses. The record survives crashes and errors so operators can
    inspect state after the fact. Never delete: this is an audit table.
    """

    class Meta:
        app_label = "cauldron_ai_admin"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="aair_status_idx"),
            models.Index(fields=["correlation_id"], name="aair_corr_idx"),
            models.Index(fields=["created_at"], name="aair_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(provider_name=""),
                name="adminairun_provider_name_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name="adminairun_version_positive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=_RUN_STATUS_VALUES),
                name="adminairun_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=["created", "running"])
                    | Q(completed_at__isnull=True)
                ),
                name="adminairun_no_completed_at_when_active",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=[
                        "waiting_for_approval",
                        "completed",
                        "failed",
                        "cancelled",
                    ])
                    | Q(completed_at__isnull=False)
                ),
                name="adminairun_completed_at_when_terminal",
            ),
        ]
        permissions = [
            ("use_admin_ai", "Can invoke the Admin AI assistant"),
            ("view_admin_ai_runs", "Can view Admin AI run history"),
            ("view_admin_ai_audit", "Can view Admin AI audit records"),
        ]

    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.CharField(
        max_length=32,
        choices=RUN_STATUS_CHOICES,
        default="created",
        db_index=True,
    )
    provider_name = models.CharField(max_length=128)
    provider_request_id = models.CharField(max_length=256, blank=True, default="")
    user_request = models.TextField()
    final_response = models.TextField(blank=True, default="")
    correlation_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    tool_call_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=128, blank=True, default="")
    error_summary = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)  # optimistic concurrency

    def __str__(self) -> str:
        return f"AdminAIRun({self.run_id}, {self.status})"


class AdminAIToolInvocation(models.Model):
    """One tool call attempt inside a run.

    We record every invocation, even ones that were denied by policy.
    Denials are important audit evidence and must appear in the trail.
    """

    class Meta:
        app_label = "cauldron_ai_admin"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["tool_name"], name="aai_ti_toolname_idx"),
            models.Index(fields=["status"], name="aai_ti_status_idx"),
            models.Index(fields=["risk_level"], name="aai_ti_risk_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(tool_name=""),
                name="adminaitoolinvocation_tool_name_nonempty",
            ),
            models.UniqueConstraint(
                fields=["run", "tool_call_id"],
                condition=~Q(tool_call_id=""),
                name="adminaitoolinvocation_tool_call_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=_INVOCATION_STATUS_VALUES),
                name="adminaitoolinvocation_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(risk_level__in=[v for v, _ in RISK_LEVEL_CHOICES]),
                name="adminaitoolinvocation_risk_level_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=["requested", "authorized", "running"])
                    | Q(completed_at__isnull=True)
                ),
                name="adminaitoolinvocation_no_completed_at_when_active",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=["completed", "denied", "failed", "timed_out"])
                    | Q(completed_at__isnull=False)
                ),
                name="adminaitoolinvocation_completed_at_when_terminal",
            ),
        ]

    invocation_id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    run = models.ForeignKey(
        AdminAIRun,
        on_delete=models.PROTECT,
        related_name="invocations",
    )
    tool_call_id = models.CharField(max_length=256, blank=True, default="")
    tool_name = models.CharField(max_length=128, db_index=True)
    tool_version = models.CharField(max_length=32, blank=True, default="")
    owning_module = models.CharField(max_length=128, blank=True, default="")
    required_permission = models.CharField(max_length=256, blank=True, default="")
    correlation_id = models.CharField(max_length=128, blank=True, default="")
    risk_level = models.CharField(
        max_length=32,
        choices=RISK_LEVEL_CHOICES,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=INVOCATION_STATUS_CHOICES,
        db_index=True,
    )
    arguments_hash = models.CharField(max_length=64, blank=True, default="")
    argument_summary = models.CharField(max_length=512, blank=True, default="")
    result_summary = models.CharField(max_length=1024, blank=True, default="")
    error_code = models.CharField(max_length=128, blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"AdminAIToolInvocation({self.tool_name}, {self.status})"
