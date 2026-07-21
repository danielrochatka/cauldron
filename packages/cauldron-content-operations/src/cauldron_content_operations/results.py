"""Immutable result types for ContentOperationService."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class OperationError:
    code: str
    message: str
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": list(self.details)}


@dataclass(frozen=True)
class ChangeRequestResult:
    """Returned by service mutation methods."""
    ok: bool
    request_id: str = ""
    lifecycle_state: str = ""
    request_version: int = 0
    error: Optional[OperationError] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "request_id": self.request_id,
            "lifecycle_state": self.lifecycle_state,
            "request_version": self.request_version,
        }
        if self.error:
            d["error"] = self.error.to_dict()
        d["meta"] = dict(self.meta)
        return d


@dataclass(frozen=True)
class ContentItemResult:
    id: str
    collection: str
    slug: str
    status: str
    schema: str
    data: dict[str, Any]
    body: str
    hash: str
    provider: str
    source_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", dict(self.data))

    @classmethod
    def from_item(cls, item: Any) -> "ContentItemResult":
        return cls(
            id=item.id,
            collection=item.collection,
            slug=item.slug,
            status=item.status.value,
            schema=item.schema,
            data=dict(item.data),
            body=item.body,
            hash=item.hash,
            provider=item.provider,
            source_ref=item.source_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "collection": self.collection,
            "slug": self.slug,
            "status": self.status,
            "schema": self.schema,
            "data": dict(self.data),
            "body": self.body,
            "hash": self.hash,
            "provider": self.provider,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class ChangeRequestDetail:
    request_id: str
    workspace_changeset_id: str
    provider_name: str
    lifecycle_state: str
    request_version: int
    payload_hash: str
    idempotency_key: str
    created_by_id: Optional[int]
    validated_by_id: Optional[int]
    approved_by_id: Optional[int]
    rejected_by_id: Optional[int]
    applied_by_id: Optional[int]
    rolled_back_by_id: Optional[int]
    created_at: Optional[str]
    validated_at: Optional[str]
    approved_at: Optional[str]
    rejected_at: Optional[str]
    applied_at: Optional[str]
    rolled_back_at: Optional[str]
    last_error_code: str = ""
    last_error_summary: str = ""
    application_result_meta: dict[str, Any] = field(default_factory=dict)
    reconciliation_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_result_meta", dict(self.application_result_meta))
        object.__setattr__(self, "reconciliation_meta", dict(self.reconciliation_meta))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workspace_changeset_id": self.workspace_changeset_id,
            "provider_name": self.provider_name,
            "lifecycle_state": self.lifecycle_state,
            "request_version": self.request_version,
            "payload_hash": self.payload_hash,
            "idempotency_key": self.idempotency_key,
            "created_by_id": self.created_by_id,
            "validated_by_id": self.validated_by_id,
            "approved_by_id": self.approved_by_id,
            "rejected_by_id": self.rejected_by_id,
            "applied_by_id": self.applied_by_id,
            "rolled_back_by_id": self.rolled_back_by_id,
            "created_at": self.created_at,
            "validated_at": self.validated_at,
            "approved_at": self.approved_at,
            "rejected_at": self.rejected_at,
            "applied_at": self.applied_at,
            "rolled_back_at": self.rolled_back_at,
            "last_error_code": self.last_error_code,
            "last_error_summary": self.last_error_summary,
            "application_result_meta": dict(self.application_result_meta),
            "reconciliation_meta": dict(self.reconciliation_meta),
        }


@dataclass(frozen=True)
class AuditEventDetail:
    event_id: str
    change_request_id: str
    sequence: int
    event_type: str
    actor_id: Optional[int]
    occurred_at: str
    previous_state: str
    resulting_state: str
    provider: str
    detail: dict[str, Any]
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", dict(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "change_request_id": self.change_request_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "occurred_at": self.occurred_at,
            "previous_state": self.previous_state,
            "resulting_state": self.resulting_state,
            "provider": self.provider,
            "detail": dict(self.detail),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class ChangeSetPreview:
    request_id: str
    operations: tuple["OperationPreview", ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "operations": [op.to_dict() for op in self.operations],
        }


@dataclass(frozen=True)
class OperationPreview:
    operation_type: str
    collection: str
    item_id: str
    provider: str
    current_hash: str
    proposed_hash: str
    current_data: dict[str, Any]
    proposed_data: dict[str, Any]
    current_body: str
    proposed_body: str
    validation_result: Optional[dict[str, Any]]
    has_conflict: bool
    diff_summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_data", dict(self.current_data))
        object.__setattr__(self, "proposed_data", dict(self.proposed_data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_type": self.operation_type,
            "collection": self.collection,
            "item_id": self.item_id,
            "provider": self.provider,
            "current_hash": self.current_hash,
            "proposed_hash": self.proposed_hash,
            "current_data": dict(self.current_data),
            "proposed_data": dict(self.proposed_data),
            "current_body": self.current_body,
            "proposed_body": self.proposed_body,
            "validation_result": self.validation_result,
            "has_conflict": self.has_conflict,
            "diff_summary": self.diff_summary,
        }
