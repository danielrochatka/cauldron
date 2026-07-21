"""Durable operational models for content change requests and audit events."""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from .lifecycle import LifecycleState


def _uuid_str() -> str:
    return str(uuid.uuid4())


class ContentChangeRequest(models.Model):
    """Durable record of a content change request lifecycle."""

    class Meta:
        app_label = "cauldron_content_operations"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lifecycle_state"], name="ccr_state_idx"),
            models.Index(fields=["provider_name"], name="ccr_provider_idx"),
            models.Index(fields=["idempotency_key"], name="ccr_idempotency_idx"),
            models.Index(fields=["created_at"], name="ccr_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False) & ~models.Q(idempotency_key=""),
                name="ccr_unique_idempotency_key",
            ),
        ]

    # Identity
    request_id = models.CharField(max_length=64, unique=True, default=_uuid_str, db_index=True)
    workspace_changeset_id = models.CharField(max_length=64, db_index=True)
    provider_name = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=255, blank=True, default="", db_index=True)

    # Lifecycle
    lifecycle_state = models.CharField(
        max_length=32,
        choices=[(s.value, s.value) for s in LifecycleState],
        default=LifecycleState.PROPOSED.value,
        db_index=True,
    )
    request_version = models.PositiveIntegerField(default=1)
    payload_hash = models.CharField(max_length=64, blank=True, default="")

    # Actors (nullable FK to allow user deletion without cascading)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_created",
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_validated",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_approved",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_rejected",
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_applied",
    )
    rolled_back_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_requests_rolled_back",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    # Error state
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    last_error_summary = models.TextField(blank=True, default="")

    # Result metadata (JSON, not payload duplication)
    application_result_meta = models.JSONField(default=dict, blank=True)
    reconciliation_meta = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"ContentChangeRequest({self.request_id}, {self.lifecycle_state})"

    @property
    def current_state(self) -> LifecycleState:
        return LifecycleState(self.lifecycle_state)


class ContentAuditEvent(models.Model):
    """Append-only audit log for content change request actions."""

    class Meta:
        app_label = "cauldron_content_operations"
        ordering = ["change_request", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["change_request", "sequence"],
                name="cae_unique_request_sequence",
            ),
        ]
        indexes = [
            models.Index(fields=["change_request", "sequence"], name="cae_request_seq_idx"),
            models.Index(fields=["event_type"], name="cae_event_type_idx"),
            models.Index(fields=["occurred_at"], name="cae_occurred_idx"),
        ]

    event_id = models.CharField(max_length=64, unique=True, default=_uuid_str, db_index=True)
    change_request = models.ForeignKey(
        ContentChangeRequest,
        on_delete=models.PROTECT,
        related_name="audit_events",
        db_index=True,
    )
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=64, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="content_audit_events",
    )
    occurred_at = models.DateTimeField(auto_now_add=True)
    previous_state = models.CharField(max_length=32, blank=True, default="")
    resulting_state = models.CharField(max_length=32, blank=True, default="")
    provider = models.CharField(max_length=128, blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    def __str__(self) -> str:
        return f"ContentAuditEvent({self.event_id}, {self.event_type}, seq={self.sequence})"


class ContentPermissionProxy(models.Model):
    """Proxy model to host custom content operation permissions."""

    class Meta:
        app_label = "cauldron_content_operations"
        managed = False
        default_permissions = ()
        permissions = (
            ("view_published_content", "Can view published content"),
            ("view_draft_content", "Can view draft content"),
            ("propose_content_changes", "Can propose content changes"),
            ("validate_content_changes", "Can validate content changes"),
            ("approve_content_changes", "Can approve content changes"),
            ("reject_content_changes", "Can reject content changes"),
            ("apply_content_changes", "Can apply content changes"),
            ("rollback_content_changes", "Can roll back content changes"),
            ("view_content_audit", "Can view content audit history"),
        )
