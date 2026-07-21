"""ContentOperationService — the single application service for permissioned content mutations."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from django.db import transaction

from .audit import AuditEventType, append_audit_event
from .config import ContentOperationsConfig, get_operations_config
from .lifecycle import LifecycleError, LifecycleState, assert_transition
from .locking import request_lock
from .models import ContentAuditEvent, ContentChangeRequest
from .results import (
    AuditEventDetail,
    ChangeRequestDetail,
    ChangeRequestResult,
    ChangeSetPreview,
    ContentItemResult,
    OperationError,
    OperationPreview,
)


class PermissionDenied(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConflictError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(Exception):
    pass


def _check_permission(user: Any, perm: str) -> None:
    if user is None:
        raise PermissionDenied("auth.not_authenticated", "Authentication required.")
    if not user.is_active:
        raise PermissionDenied("auth.inactive", "User account is inactive.")
    perm_str = f"cauldron_content_operations.{perm}"
    if not user.has_perm(perm_str):
        raise PermissionDenied(
            "auth.permission_denied",
            f"Permission {perm_str!r} is required.",
        )


def _ts(dt: Optional[Any]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _build_detail(change_request: ContentChangeRequest) -> ChangeRequestDetail:
    def uid(fk: Any) -> Optional[int]:
        try:
            return fk.pk if fk is not None else None
        except Exception:
            return None

    return ChangeRequestDetail(
        request_id=change_request.request_id,
        workspace_changeset_id=change_request.workspace_changeset_id,
        provider_name=change_request.provider_name,
        lifecycle_state=change_request.lifecycle_state,
        request_version=change_request.request_version,
        payload_hash=change_request.payload_hash,
        idempotency_key=change_request.idempotency_key,
        created_by_id=uid(change_request.created_by),
        validated_by_id=uid(change_request.validated_by),
        approved_by_id=uid(change_request.approved_by),
        rejected_by_id=uid(change_request.rejected_by),
        applied_by_id=uid(change_request.applied_by),
        rolled_back_by_id=uid(change_request.rolled_back_by),
        created_at=_ts(change_request.created_at),
        validated_at=_ts(change_request.validated_at),
        approved_at=_ts(change_request.approved_at),
        rejected_at=_ts(change_request.rejected_at),
        applied_at=_ts(change_request.applied_at),
        rolled_back_at=_ts(change_request.rolled_back_at),
        last_error_code=change_request.last_error_code,
        last_error_summary=change_request.last_error_summary,
        application_result_meta=dict(change_request.application_result_meta or {}),
        reconciliation_meta=dict(change_request.reconciliation_meta or {}),
    )


def _compute_payload_hash(operations_data: list[dict]) -> str:
    payload_str = json.dumps(operations_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload_str.encode()).hexdigest()


class ContentOperationService:
    """
    The single application service for permissioned content mutations.

    Callers must supply an authenticated Django user. Permissions are enforced
    centrally here. API and Admin modules must not re-implement authorization.
    """

    def __init__(
        self,
        *,
        router: Any,          # ContentRouter
        workspace: Any,       # ChangeSetStore (optional, may be None)
        snapshots: Any,       # SnapshotService (optional, may be None)
        config: Optional[ContentOperationsConfig] = None,
    ) -> None:
        self._router = router
        self._workspace = workspace
        self._snapshots = snapshots
        self._config = config or get_operations_config()

    # -------------------------------------------------------------------------
    # Private workspace helpers
    # -------------------------------------------------------------------------

    def _read_cs_payload(self, cs_id: str) -> Optional[dict]:
        """Read payload.json from workspace for a changeset."""
        if self._workspace is None:
            return None
        try:
            import json as _json
            from cauldron_workspace_flatfile.paths import safe_resolve
            cs_dir = safe_resolve(self._workspace._config.change_sets_dir, cs_id)
            with open(cs_dir / "payload.json", "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return None

    def _read_cs_result(self, cs_id: str) -> Optional[dict]:
        """Read result.json from workspace for a changeset."""
        if self._workspace is None:
            return None
        try:
            import json as _json
            from cauldron_workspace_flatfile.paths import safe_resolve
            cs_dir = safe_resolve(self._workspace._config.change_sets_dir, cs_id)
            result_path = cs_dir / "result.json"
            if not result_path.exists():
                return None
            with open(result_path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def list_collections(self, *, user: Any) -> list[str]:
        _check_permission(user, "view_published_content")
        try:
            from cauldron_content.registry import registry
            collections: set[str] = set()
            for provider_name in registry.names():
                repo = registry.get(provider_name)
                if repo is not None:
                    collections.update(repo.list_collections())
            return sorted(collections)
        except Exception:
            return []

    def list_items(
        self,
        collection: str,
        *,
        user: Any,
        include_drafts: bool = False,
    ) -> list[ContentItemResult]:
        _check_permission(user, "view_published_content")
        if include_drafts:
            _check_permission(user, "view_draft_content")
        items = self._router.list_items(collection, include_drafts=include_drafts)
        return [ContentItemResult.from_item(item) for item in items]

    def get_item(
        self,
        item_id: str,
        collection: str,
        *,
        user: Any,
        include_drafts: bool = False,
    ) -> Optional[ContentItemResult]:
        _check_permission(user, "view_published_content")
        if include_drafts:
            _check_permission(user, "view_draft_content")
        item = self._router.get_by_id(item_id, collection, include_drafts=include_drafts)
        if item is None:
            return None
        return ContentItemResult.from_item(item)

    # -------------------------------------------------------------------------
    # Change request management
    # -------------------------------------------------------------------------

    def create_change_request(
        self,
        *,
        user: Any,
        operations: list[dict[str, Any]],
        provider_name: str,
        description: str = "",
        idempotency_key: str = "",
    ) -> ChangeRequestResult:
        _check_permission(user, "propose_content_changes")

        cfg = self._config
        if len(operations) > cfg.max_operations_per_change_set:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    code="operations.too_many",
                    message=f"Too many operations: maximum is {cfg.max_operations_per_change_set}.",
                ),
            )

        # Idempotency check
        if idempotency_key:
            existing = ContentChangeRequest.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return ChangeRequestResult(
                    ok=True,
                    request_id=existing.request_id,
                    lifecycle_state=existing.lifecycle_state,
                    request_version=existing.request_version,
                    meta={"idempotent": True},
                )

        payload_hash = _compute_payload_hash(operations)
        request_id = str(uuid.uuid4())
        cs_id = str(uuid.uuid4())

        if self._workspace is not None:
            try:
                from cauldron_content.contracts import (
                    ContentChangeSet,
                    ContentOperation,
                    ContentOperationKind,
                    ContentStatus,
                )

                ops = []
                for op_data in operations:
                    kind_str = op_data.get("kind", "create")
                    try:
                        kind = ContentOperationKind(kind_str)
                    except ValueError:
                        return ChangeRequestResult(
                            ok=False,
                            error=OperationError(
                                code="operations.invalid_kind",
                                message=f"Invalid operation kind: {kind_str!r}",
                            ),
                        )
                    status_str = op_data.get("status", "draft")
                    try:
                        status = ContentStatus(status_str)
                    except ValueError:
                        status = ContentStatus.DRAFT

                    ops.append(ContentOperation(
                        kind=kind,
                        provider=op_data.get("provider", provider_name),
                        collection=op_data.get("collection", ""),
                        item_id=op_data.get("item_id", ""),
                        slug=op_data.get("slug", ""),
                        expected_hash=op_data.get("expected_hash", ""),
                        data=op_data.get("data", {}),
                        body=op_data.get("body", ""),
                        schema=op_data.get("schema", ""),
                        status=status,
                        force=bool(op_data.get("force", False)),
                    ))

                changeset = ContentChangeSet(
                    id=cs_id,
                    operations=tuple(ops),
                    author=str(user.get_username()) if hasattr(user, "get_username") else str(user),
                    description=description,
                )
                self._workspace.create(changeset)
            except ChangeRequestResult:
                raise
            except Exception as exc:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        code="workspace.create_failed",
                        message="Failed to create workspace change set.",
                        details=(str(exc)[:200],),
                    ),
                )

        with transaction.atomic():
            cr = ContentChangeRequest.objects.create(
                request_id=request_id,
                workspace_changeset_id=cs_id,
                provider_name=provider_name,
                lifecycle_state=LifecycleState.PROPOSED.value,
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
                created_by=user if hasattr(user, "pk") else None,
            )
            append_audit_event(
                change_request=cr,
                event_type=AuditEventType.PROPOSAL_CREATED,
                actor=user,
                resulting_state=LifecycleState.PROPOSED.value,
                provider=provider_name,
                correlation_id=request_id,
            )

        return ChangeRequestResult(
            ok=True,
            request_id=request_id,
            lifecycle_state=LifecycleState.PROPOSED.value,
            request_version=1,
        )

    def get_change_request(self, request_id: str, *, user: Any) -> Optional[ChangeRequestDetail]:
        _check_permission(user, "view_published_content")
        try:
            cr = ContentChangeRequest.objects.get(request_id=request_id)
        except ContentChangeRequest.DoesNotExist:
            return None
        return _build_detail(cr)

    def list_change_requests(
        self,
        *,
        user: Any,
        lifecycle_state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChangeRequestDetail]:
        _check_permission(user, "view_published_content")
        qs = ContentChangeRequest.objects.all()
        if lifecycle_state:
            qs = qs.filter(lifecycle_state=lifecycle_state)
        qs = qs.order_by("-created_at")[offset: offset + limit]
        return [_build_detail(cr) for cr in qs]

    def validate_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        _check_permission(user, "validate_content_changes")
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError("not_found", f"Change request {request_id!r} not found."),
                )

            if expected_version and cr.request_version != expected_version:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "conflict.version",
                        f"Version conflict: expected {expected_version}, got {cr.request_version}.",
                    ),
                )

            current_state = cr.current_state
            try:
                assert_transition(current_state, LifecycleState.VALIDATED)
            except LifecycleError as exc:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={"error_code": exc.code, "error": exc.message},
                )
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(exc.code, exc.message),
                )

            append_audit_event(
                change_request=cr,
                event_type=AuditEventType.VALIDATION_REQUESTED,
                actor=user,
                previous_state=current_state.value,
                resulting_state=current_state.value,
                correlation_id=correlation_id,
            )

            # Load and validate via router
            cs_data = self._read_cs_payload(cr.workspace_changeset_id)

            validation_issues = []
            if cs_data:
                try:
                    from cauldron_content.contracts import (
                        ContentChangeSet,
                        ContentOperation,
                        ContentOperationKind,
                        ContentStatus,
                    )
                    ops = []
                    for op_data in cs_data.get("operations", []):
                        kind = ContentOperationKind(op_data["kind"])
                        status = ContentStatus(op_data.get("status", "draft"))
                        ops.append(ContentOperation(
                            kind=kind,
                            provider=op_data.get("provider", ""),
                            collection=op_data.get("collection", ""),
                            item_id=op_data.get("item_id", ""),
                            slug=op_data.get("slug", ""),
                            expected_hash=op_data.get("expected_hash", ""),
                            data=op_data.get("data", {}),
                            body=op_data.get("body", ""),
                            schema=op_data.get("schema", ""),
                            status=status,
                            force=op_data.get("force", False),
                        ))
                    # Basic structural validation (schema validation via provider)
                    for op in ops:
                        if not op.collection:
                            validation_issues.append({"code": "missing_collection", "item_id": op.item_id})
                        if not op.item_id:
                            validation_issues.append({"code": "missing_item_id", "collection": op.collection})
                except Exception as exc:
                    validation_issues.append({"code": "parse_error", "detail": str(exc)[:200]})

            if validation_issues:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={"issues": validation_issues[:10]},
                )
                cr.last_error_code = "validation.failed"
                cr.last_error_summary = f"{len(validation_issues)} validation issue(s)."
                cr.save(update_fields=["last_error_code", "last_error_summary", "updated_at"])
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "validation.failed",
                        f"Validation failed: {len(validation_issues)} issue(s).",
                        details=tuple(str(i) for i in validation_issues[:5]),
                    ),
                )

            cr.lifecycle_state = LifecycleState.VALIDATED.value
            cr.request_version += 1
            cr.validated_by = user if hasattr(user, "pk") else None
            cr.validated_at = datetime.now(timezone.utc)
            cr.last_error_code = ""
            cr.last_error_summary = ""
            cr.save(update_fields=[
                "lifecycle_state", "request_version", "validated_by",
                "validated_at", "last_error_code", "last_error_summary", "updated_at",
            ])

            append_audit_event(
                change_request=cr,
                event_type=AuditEventType.VALIDATION_SUCCEEDED,
                actor=user,
                previous_state=current_state.value,
                resulting_state=LifecycleState.VALIDATED.value,
                provider=cr.provider_name,
                correlation_id=correlation_id,
            )

        return ChangeRequestResult(
            ok=True,
            request_id=request_id,
            lifecycle_state=LifecycleState.VALIDATED.value,
            request_version=cr.request_version,
        )

    def approve_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        cfg = self._config
        _check_permission(user, "approve_content_changes")
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

            if expected_version and cr.request_version != expected_version:
                return ChangeRequestResult(ok=False, error=OperationError("conflict.version", f"Version conflict: expected {expected_version}, got {cr.request_version}."))

            # Self-approval check
            if not cfg.allow_self_approval and hasattr(user, "pk") and cr.created_by_id == user.pk:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.APPROVAL_DENIED,
                    actor=user,
                    previous_state=cr.lifecycle_state,
                    resulting_state=cr.lifecycle_state,
                    correlation_id=correlation_id,
                    detail={"reason": "self_approval_not_allowed"},
                )
                return ChangeRequestResult(ok=False, error=OperationError("approval.self_approval_denied", "Self-approval is not permitted."))

            current_state = cr.current_state
            try:
                assert_transition(current_state, LifecycleState.APPROVED)
            except LifecycleError as exc:
                return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

            cr.lifecycle_state = LifecycleState.APPROVED.value
            cr.request_version += 1
            cr.approved_by = user if hasattr(user, "pk") else None
            cr.approved_at = datetime.now(timezone.utc)
            cr.save(update_fields=["lifecycle_state", "request_version", "approved_by", "approved_at", "updated_at"])

            append_audit_event(
                change_request=cr,
                event_type=AuditEventType.APPROVAL_GRANTED,
                actor=user,
                previous_state=current_state.value,
                resulting_state=LifecycleState.APPROVED.value,
                provider=cr.provider_name,
                correlation_id=correlation_id,
            )

        return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.APPROVED.value, request_version=cr.request_version)

    def reject_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        reason: str = "",
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        _check_permission(user, "reject_content_changes")
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

            if expected_version and cr.request_version != expected_version:
                return ChangeRequestResult(ok=False, error=OperationError("conflict.version", "Version conflict."))

            current_state = cr.current_state
            try:
                assert_transition(current_state, LifecycleState.REJECTED)
            except LifecycleError as exc:
                return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

            cr.lifecycle_state = LifecycleState.REJECTED.value
            cr.request_version += 1
            cr.rejected_by = user if hasattr(user, "pk") else None
            cr.rejected_at = datetime.now(timezone.utc)
            cr.save(update_fields=["lifecycle_state", "request_version", "rejected_by", "rejected_at", "updated_at"])

            append_audit_event(
                change_request=cr,
                event_type=AuditEventType.PROPOSAL_REJECTED,
                actor=user,
                previous_state=current_state.value,
                resulting_state=LifecycleState.REJECTED.value,
                provider=cr.provider_name,
                correlation_id=correlation_id,
                detail={"reason": reason[:500] if reason else ""},
            )

        return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.REJECTED.value, request_version=cr.request_version)

    def apply_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        _check_permission(user, "apply_content_changes")
        cfg = self._config
        correlation_id = str(uuid.uuid4())

        # Idempotency: already applied
        try:
            cr_check = ContentChangeRequest.objects.get(request_id=request_id)
            if cr_check.current_state == LifecycleState.APPLIED:
                return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.APPLIED.value, request_version=cr_check.request_version, meta={"idempotent": True})
        except ContentChangeRequest.DoesNotExist:
            return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

        with request_lock(request_id):
            # Step 2-7: Mark as applying inside a transaction
            with transaction.atomic():
                try:
                    cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
                except ContentChangeRequest.DoesNotExist:
                    return ChangeRequestResult(ok=False, error=OperationError("not_found", "Not found."))

                if expected_version and cr.request_version != expected_version:
                    return ChangeRequestResult(ok=False, error=OperationError("conflict.version", "Version conflict."))

                current_state = cr.current_state

                # If require_approval is True, must be APPROVED; if False, VALIDATED is also acceptable
                if cfg.require_approval:
                    if current_state != LifecycleState.APPROVED:
                        try:
                            assert_transition(current_state, LifecycleState.APPLYING)
                        except LifecycleError as exc:
                            return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))
                else:
                    allowed = {LifecycleState.APPROVED, LifecycleState.VALIDATED}
                    if current_state not in allowed:
                        try:
                            assert_transition(current_state, LifecycleState.APPLYING)
                        except LifecycleError as exc:
                            return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

                cr.lifecycle_state = LifecycleState.APPLYING.value
                cr.request_version += 1
                cr.applied_by = user if hasattr(user, "pk") else None
                cr.save(update_fields=["lifecycle_state", "request_version", "applied_by", "updated_at"])

                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.APPLICATION_STARTED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=LifecycleState.APPLYING.value,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                )
            # Transaction committed — applying state is durable

            # Step 8-10: Apply through router
            try:
                cs_data = self._read_cs_payload(cr.workspace_changeset_id)
                if cs_data is None:
                    raise ValueError("No payload data found for changeset.")
                from cauldron_content.contracts import (
                    ContentChangeSet,
                    ContentOperation,
                    ContentOperationKind,
                    ContentStatus,
                )
                ops = []
                for op_data in cs_data.get("operations", []):
                    kind = ContentOperationKind(op_data["kind"])
                    status = ContentStatus(op_data.get("status", "draft"))
                    ops.append(ContentOperation(
                        kind=kind,
                        provider=op_data.get("provider", ""),
                        collection=op_data.get("collection", ""),
                        item_id=op_data.get("item_id", ""),
                        slug=op_data.get("slug", ""),
                        expected_hash=op_data.get("expected_hash", ""),
                        data=op_data.get("data", {}),
                        body=op_data.get("body", ""),
                        schema=op_data.get("schema", ""),
                        status=status,
                        force=op_data.get("force", False),
                    ))
                changeset = ContentChangeSet(
                    id=cr.workspace_changeset_id,
                    operations=tuple(ops),
                )
                apply_result = self._router.apply(changeset)
            except Exception as exc:
                # Application failed
                with transaction.atomic():
                    cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                    cr2.lifecycle_state = LifecycleState.APPLY_FAILED.value
                    cr2.request_version += 1
                    cr2.last_error_code = "application.exception"
                    cr2.last_error_summary = str(exc)[:500]
                    cr2.save(update_fields=["lifecycle_state", "request_version", "last_error_code", "last_error_summary", "updated_at"])
                    append_audit_event(
                        change_request=cr2,
                        event_type=AuditEventType.APPLICATION_FAILED,
                        actor=user,
                        previous_state=LifecycleState.APPLYING.value,
                        resulting_state=LifecycleState.APPLY_FAILED.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail={"error_code": "application.exception", "error_summary": str(exc)[:200]},
                    )
                return ChangeRequestResult(ok=False, error=OperationError("application.exception", "Application failed."), request_id=request_id, lifecycle_state=LifecycleState.APPLY_FAILED.value)

            if not apply_result.success:
                error_detail = {
                    "conflicts": len(apply_result.conflicts),
                    "validation_errors": len(apply_result.validation_errors),
                }
                with transaction.atomic():
                    cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                    cr2.lifecycle_state = LifecycleState.APPLY_FAILED.value
                    cr2.request_version += 1
                    cr2.last_error_code = "application.conflicts"
                    cr2.last_error_summary = f"Conflicts: {len(apply_result.conflicts)}, Validation errors: {len(apply_result.validation_errors)}"
                    cr2.application_result_meta = error_detail
                    cr2.save(update_fields=["lifecycle_state", "request_version", "last_error_code", "last_error_summary", "application_result_meta", "updated_at"])
                    append_audit_event(
                        change_request=cr2,
                        event_type=AuditEventType.APPLICATION_FAILED,
                        actor=user,
                        previous_state=LifecycleState.APPLYING.value,
                        resulting_state=LifecycleState.APPLY_FAILED.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail=error_detail,
                    )
                return ChangeRequestResult(ok=False, error=OperationError("application.conflicts", cr2.last_error_summary), request_id=request_id, lifecycle_state=LifecycleState.APPLY_FAILED.value)

            # Save workspace result
            result_meta = {
                "applied_count": len(apply_result.applied),
                "correlation_id": correlation_id,
            }
            try:
                if self._workspace is not None:
                    self._workspace.save_result(cr.workspace_changeset_id, result_meta)
            except Exception:
                pass  # Non-fatal — state will be marked applied below

            # Steps 11-16: Mark applied
            with transaction.atomic():
                cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                cr2.lifecycle_state = LifecycleState.APPLIED.value
                cr2.request_version += 1
                cr2.applied_at = datetime.now(timezone.utc)
                cr2.application_result_meta = result_meta
                cr2.last_error_code = ""
                cr2.last_error_summary = ""
                cr2.save(update_fields=[
                    "lifecycle_state", "request_version", "applied_at",
                    "application_result_meta", "last_error_code", "last_error_summary", "updated_at",
                ])
                append_audit_event(
                    change_request=cr2,
                    event_type=AuditEventType.APPLICATION_SUCCEEDED,
                    actor=user,
                    previous_state=LifecycleState.APPLYING.value,
                    resulting_state=LifecycleState.APPLIED.value,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail=result_meta,
                )

        return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.APPLIED.value, request_version=cr2.request_version)

    def rollback_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        force: bool = False,
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        _check_permission(user, "rollback_content_changes")
        correlation_id = str(uuid.uuid4())

        try:
            cr_check = ContentChangeRequest.objects.get(request_id=request_id)
            if cr_check.current_state == LifecycleState.ROLLED_BACK:
                return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.ROLLED_BACK.value, request_version=cr_check.request_version, meta={"idempotent": True})
        except ContentChangeRequest.DoesNotExist:
            return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

        with request_lock(request_id):
            with transaction.atomic():
                try:
                    cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
                except ContentChangeRequest.DoesNotExist:
                    return ChangeRequestResult(ok=False, error=OperationError("not_found", "Not found."))

                if expected_version and cr.request_version != expected_version:
                    return ChangeRequestResult(ok=False, error=OperationError("conflict.version", "Version conflict."))

                current_state = cr.current_state
                try:
                    assert_transition(current_state, LifecycleState.ROLLING_BACK)
                except LifecycleError as exc:
                    return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

                cr.lifecycle_state = LifecycleState.ROLLING_BACK.value
                cr.request_version += 1
                cr.rolled_back_by = user if hasattr(user, "pk") else None
                cr.save(update_fields=["lifecycle_state", "request_version", "rolled_back_by", "updated_at"])

                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.ROLLBACK_STARTED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=LifecycleState.ROLLING_BACK.value,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                )

            # Perform rollback via snapshots
            try:
                if self._snapshots is not None:
                    self._snapshots.rollback(cr.workspace_changeset_id, force=force)
                rollback_ok = True
                rollback_error = ""
            except Exception as exc:
                rollback_ok = False
                rollback_error = str(exc)[:500]

            with transaction.atomic():
                cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                if rollback_ok:
                    cr2.lifecycle_state = LifecycleState.ROLLED_BACK.value
                    cr2.rolled_back_at = datetime.now(timezone.utc)
                    cr2.last_error_code = ""
                    cr2.last_error_summary = ""
                    cr2.request_version += 1
                    cr2.save(update_fields=["lifecycle_state", "rolled_back_at", "last_error_code", "last_error_summary", "request_version", "updated_at"])
                    append_audit_event(
                        change_request=cr2,
                        event_type=AuditEventType.ROLLBACK_SUCCEEDED,
                        actor=user,
                        previous_state=LifecycleState.ROLLING_BACK.value,
                        resulting_state=LifecycleState.ROLLED_BACK.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                    )
                else:
                    cr2.lifecycle_state = LifecycleState.ROLLBACK_FAILED.value
                    cr2.last_error_code = "rollback.failed"
                    cr2.last_error_summary = rollback_error
                    cr2.request_version += 1
                    cr2.save(update_fields=["lifecycle_state", "last_error_code", "last_error_summary", "request_version", "updated_at"])
                    append_audit_event(
                        change_request=cr2,
                        event_type=AuditEventType.ROLLBACK_FAILED,
                        actor=user,
                        previous_state=LifecycleState.ROLLING_BACK.value,
                        resulting_state=LifecycleState.ROLLBACK_FAILED.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail={"error_summary": rollback_error},
                    )

        if rollback_ok:
            return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.ROLLED_BACK.value, request_version=cr2.request_version)
        return ChangeRequestResult(ok=False, error=OperationError("rollback.failed", rollback_error), request_id=request_id, lifecycle_state=LifecycleState.ROLLBACK_FAILED.value)

    def get_audit_history(
        self,
        request_id: str,
        *,
        user: Any,
    ) -> list[AuditEventDetail]:
        _check_permission(user, "view_content_audit")
        try:
            cr = ContentChangeRequest.objects.get(request_id=request_id)
        except ContentChangeRequest.DoesNotExist:
            return []
        events = ContentAuditEvent.objects.filter(change_request=cr).order_by("sequence")
        return [
            AuditEventDetail(
                event_id=e.event_id,
                change_request_id=request_id,
                sequence=e.sequence,
                event_type=e.event_type,
                actor_id=e.actor_id,
                occurred_at=e.occurred_at.isoformat(),
                previous_state=e.previous_state,
                resulting_state=e.resulting_state,
                provider=e.provider,
                detail=dict(e.detail or {}),
                correlation_id=e.correlation_id,
            )
            for e in events
        ]

    def get_preview(
        self,
        request_id: str,
        *,
        user: Any,
    ) -> Optional[ChangeSetPreview]:
        _check_permission(user, "view_published_content")
        try:
            cr = ContentChangeRequest.objects.get(request_id=request_id)
        except ContentChangeRequest.DoesNotExist:
            return None

        cs_data = self._read_cs_payload(cr.workspace_changeset_id)
        if cs_data is None:
            return None

        previews = []
        for op_data in cs_data.get("operations", []):
            item_id = op_data.get("item_id", "")
            collection = op_data.get("collection", "")
            current_item = None
            try:
                current_item = self._router.get_by_id(item_id, collection, include_drafts=True)
            except Exception:
                pass

            from cauldron_content.hashing import compute_content_hash
            from cauldron_content.contracts import ContentStatus
            proposed_data = op_data.get("data", {})
            proposed_body = op_data.get("body", "")
            proposed_slug = op_data.get("slug", item_id)

            # Compute proposed hash using canonical function
            try:
                status_str = op_data.get("status", "draft")
                proposed_hash = compute_content_hash(
                    item_id=item_id,
                    collection=collection,
                    slug=proposed_slug,
                    status=status_str,
                    schema=op_data.get("schema", ""),
                    data=proposed_data,
                    body=proposed_body,
                )
            except Exception:
                proposed_hash = ""

            # Simple text diff summary (escaped)
            import html
            current_body = current_item.body if current_item else ""
            current_data = dict(current_item.data) if current_item else {}
            current_hash = current_item.hash if current_item else ""
            has_conflict = bool(op_data.get("expected_hash") and current_item and op_data["expected_hash"] != current_hash)

            diff_lines = []
            if current_body != proposed_body:
                diff_lines.append(f"Body changed ({len(current_body)} -> {len(proposed_body)} chars)")
            if current_data != proposed_data:
                diff_lines.append("Structured data changed")
            diff_summary = html.escape("; ".join(diff_lines) if diff_lines else "No text changes")

            previews.append(OperationPreview(
                operation_type=op_data.get("kind", ""),
                collection=collection,
                item_id=item_id,
                provider=op_data.get("provider", cr.provider_name),
                current_hash=current_hash,
                proposed_hash=proposed_hash,
                current_data=current_data,
                proposed_data=proposed_data,
                current_body=current_body,
                proposed_body=proposed_body,
                validation_result=None,
                has_conflict=has_conflict,
                diff_summary=diff_summary,
            ))

        return ChangeSetPreview(request_id=request_id, operations=tuple(previews))

    def reconcile(
        self,
        *,
        user: Any,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Inspect and optionally finalize interrupted change requests."""
        # Only superusers or users with apply_content_changes can reconcile
        _check_permission(user, "apply_content_changes")

        transitional = ContentChangeRequest.objects.filter(
            lifecycle_state__in=[
                LifecycleState.APPLYING.value,
                LifecycleState.ROLLING_BACK.value,
                LifecycleState.RECONCILIATION_REQUIRED.value,
            ]
        )

        results = []
        for cr in transitional:
            entry = {
                "request_id": cr.request_id,
                "current_state": cr.lifecycle_state,
                "action": None,
                "reason": "",
                "applied": False,
            }

            # Try to read workspace result
            result_data = self._read_cs_result(cr.workspace_changeset_id)

            if result_data is not None:
                # Result file exists — application likely completed
                entry["action"] = "finalize_applied"
                entry["reason"] = "Workspace result file found; application likely completed."
                if not dry_run:
                    with transaction.atomic():
                        cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                        cr2.lifecycle_state = LifecycleState.APPLIED.value
                        cr2.request_version += 1
                        cr2.applied_at = cr2.applied_at or datetime.now(timezone.utc)
                        cr2.reconciliation_meta = {"reconciled": True, "result_data": result_data}
                        cr2.save(update_fields=["lifecycle_state", "request_version", "applied_at", "reconciliation_meta", "updated_at"])
                        append_audit_event(
                            change_request=cr2,
                            event_type=AuditEventType.RECONCILIATION_COMPLETED,
                            actor=user,
                            previous_state=cr.lifecycle_state,
                            resulting_state=LifecycleState.APPLIED.value,
                            provider=cr.provider_name,
                        )
                    entry["applied"] = True
            else:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = "No workspace result file; cannot safely determine completion. Manual review required."

            results.append(entry)

        return results
