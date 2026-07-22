"""Audit event appender."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from django.db import IntegrityError
from django.db.models import Max

from .models import ContentAuditEvent, ContentChangeRequest


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
    number is computed as ``MAX(sequence) + 1`` under that lock to avoid the
    race that plain ``count()+1`` would introduce.
    """

    def _next_seq() -> int:
        max_seq = (
            ContentAuditEvent.objects.filter(change_request=change_request)
            .aggregate(m=Max("sequence"))
            .get("m")
        )
        return int(max_seq or 0) + 1

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
    try:
        event.save()
        return event
    except IntegrityError:
        # Rare race: another writer beat us on the (change_request, sequence)
        # UniqueConstraint. Recompute and try again exactly once.
        event.sequence = _next_seq()
        event.event_id = str(uuid.uuid4())
        event.save()
        return event
