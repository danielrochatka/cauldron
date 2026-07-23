"""Audit event appender."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from django.db import IntegrityError, transaction
from django.db.models import Max

from .models import ContentAuditEvent, ContentChangeRequest


class AuditSequenceError(Exception):
    """Raised when a valid audit sequence number cannot be allocated."""


class AuditEventType:
    PROPOSAL_CREATED = "proposal.created"
    VALIDATION_REQUESTED = "validation.requested"
    VALIDATION_SUCCEEDED = "validation.succeeded"
    VALIDATION_FAILED = "validation.failed"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    PROPOSAL_REJECTED = "proposal.rejected"
    APPLICATION_STARTED = "application.started"
    APPLICATION_SUCCEEDED = "application.succeeded"
    APPLICATION_FAILED = "application.failed"
    ROLLBACK_STARTED = "rollback.started"
    ROLLBACK_SUCCEEDED = "rollback.succeeded"
    ROLLBACK_FAILED = "rollback.failed"
    RECONCILIATION_STARTED = "reconciliation.started"
    RECONCILIATION_COMPLETED = "reconciliation.completed"
    RECONCILIATION_FAILED = "reconciliation.failed"
    AUTHORIZATION_DENIED = "authorization.denied"
    CONTENT_VIEWED = "content.viewed"
    # Item 9: workspace mirror-sync failure is a distinct, dedicated event so
    # dashboards don't see it as a duplicate lifecycle-success audit entry.
    WORKSPACE_SYNC_FAILED = "workspace.sync_failed"


_MAX_RETRIES = 3


def append_audit_event(
    *,
    change_request: ContentChangeRequest,
    event_type: str,
    actor: Any = None,
    previous_state: str = "",
    resulting_state: str = "",
    provider: str = "",
    detail: Optional[dict[str, Any]] = None,
    correlation_id: str = "",
) -> ContentAuditEvent:
    """Append an audit event atomically inside the current transaction.

    Must be called inside an active transaction where the caller has already
    obtained ``select_for_update()`` on the change request. The sequence
    number is computed as ``MAX(sequence) + 1`` under that lock. On
    IntegrityError from a race, the INSERT is retried inside a fresh
    savepoint so the outer transaction stays valid.
    """

    def _next_seq() -> int:
        max_seq = (
            ContentAuditEvent.objects.filter(change_request=change_request)
            .aggregate(m=Max("sequence"))
            .get("m")
        )
        return int(max_seq or 0) + 1

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            with transaction.atomic():
                event = ContentAuditEvent(
                    event_id=str(uuid.uuid4()),
                    change_request=change_request,
                    sequence=_next_seq(),
                    event_type=event_type,
                    actor=actor if (hasattr(actor, "pk") and actor.pk is not None) else None,
                    previous_state=previous_state,
                    resulting_state=resulting_state,
                    provider=provider,
                    detail=detail or {},
                    correlation_id=correlation_id,
                )
                event.save()
                return event
        except IntegrityError as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES - 1:
                raise AuditSequenceError(
                    "Failed to allocate audit sequence after retries."
                ) from exc
            # Loop and try again with a new savepoint and freshly computed sequence.
            continue

    # Should not be reachable.
    if last_exc is not None:
        raise AuditSequenceError(
            "Failed to allocate audit sequence after retries."
        ) from last_exc
    raise AuditSequenceError("Failed to allocate audit sequence after retries.")
