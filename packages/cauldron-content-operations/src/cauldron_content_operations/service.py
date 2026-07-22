"""ContentOperationService — the single application service for permissioned content mutations."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from django.db import IntegrityError, transaction

from .audit import AuditEventType, append_audit_event
from .config import ContentOperationsConfig, get_operations_config
from .lifecycle import LifecycleError, LifecycleState, assert_transition
from .locking import provider_lock, request_lock
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
from .reversible import get_adapter


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
    """Legacy hasher retained only for tests that inspect deterministic dicts.

    Production code paths use ``compute_changeset_hash`` on the persisted
    ContentChangeSet (see :func:`_compute_canonical_changeset_hash`).
    """
    payload_str = json.dumps(operations_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload_str.encode()).hexdigest()


def _compute_canonical_changeset_hash(changeset: Any) -> str:
    """Compute the canonical hash of a ContentChangeSet.

    Falls back to a local implementation if ``cauldron_workspace_flatfile`` is
    unavailable (e.g. tests without the workspace package installed).
    """
    try:
        from cauldron_workspace_flatfile.store import compute_changeset_hash
        return compute_changeset_hash(changeset)
    except Exception:
        # Fallback: hash a deterministic representation.
        ops = []
        for op in changeset.operations:
            kind = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            status = op.status.value if hasattr(op.status, "value") else str(op.status)
            ops.append({
                "body": op.body or "",
                "collection": op.collection or "",
                "data": dict(op.data or {}),
                "expected_hash": op.expected_hash or "",
                "force": False,
                "item_id": op.item_id or "",
                "kind": kind,
                "provider": op.provider or "",
                "schema": op.schema or "",
                "slug": op.slug or "",
                "status": status,
            })
        payload = {"operations": ops}
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_workspace_changeset_with_integrity(
    workspace: Any,
    cs_id: str,
    expected_hash: str,
) -> tuple[Any, Optional[OperationError]]:
    """Load a changeset and verify it against the expected canonical hash.

    Returns (changeset, None) on success, or (None, error) on integrity failure
    or force-tampering. Any operation with force=True in the persisted payload
    is rejected because public proposals must never carry force through.
    """
    # Prefer load_changeset_with_hash when the workspace supports it; fall back
    # to load_changeset + local canonical hashing otherwise. This keeps tests
    # that mock only load_changeset() working.
    try:
        if hasattr(workspace, "load_changeset_with_hash"):
            loaded = workspace.load_changeset_with_hash(cs_id)
            # MagicMock will return a MagicMock, not a tuple. Guard against that.
            if (
                isinstance(loaded, tuple)
                and len(loaded) == 2
                and isinstance(loaded[1], str)
            ):
                changeset, actual_hash = loaded
            else:
                changeset = workspace.load_changeset(cs_id)
                actual_hash = _compute_canonical_changeset_hash(changeset)
        else:
            changeset = workspace.load_changeset(cs_id)
            actual_hash = _compute_canonical_changeset_hash(changeset)
    except Exception as exc:
        return None, OperationError(
            "workspace.load_failed",
            f"Failed to load proposal: {str(exc)[:200]}",
        )

    # Detect manual force tampering.
    for op in getattr(changeset, "operations", ()) or ():
        if bool(getattr(op, "force", False)):
            return None, OperationError(
                "workspace.force_not_allowed",
                "Persisted changeset contains a force=True operation; refusing.",
            )

    if expected_hash and actual_hash and actual_hash != expected_hash:
        return None, OperationError(
            "workspace.payload_integrity_mismatch",
            "Persisted workspace payload does not match the recorded hash.",
        )
    return changeset, None


def _safe_workspace_transition(workspace: Any, cs_id: str, new_state: Any) -> None:
    """Best-effort workspace state transition, silencing recoverable errors.

    Workspace state is informational for validation/rejection/approval; SQL is
    authoritative. Callers that need to escalate a failure (apply/rollback)
    should call ``workspace.transition`` directly.
    """
    if workspace is None:
        return
    try:
        workspace.transition(cs_id, new_state)
    except Exception:
        # Do not block SQL lifecycle progression on workspace observability failures.
        pass


def _require_positive_version(expected_version: Any) -> Optional[ChangeRequestResult]:
    """Return an error result if ``expected_version`` is not a positive int."""
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        return ChangeRequestResult(
            ok=False,
            error=OperationError(
                "conflict.version_required",
                "A positive expected_version is required.",
            ),
        )
    if expected_version <= 0:
        return ChangeRequestResult(
            ok=False,
            error=OperationError(
                "conflict.version_required",
                "A positive expected_version is required.",
            ),
        )
    return None


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
        snapshots: Any = None,  # Legacy SnapshotService (retained for compat, unused by new code)
        config: Optional[ContentOperationsConfig] = None,
        locks_dir: Optional[Path] = None,
    ) -> None:
        self._router = router
        self._workspace = workspace
        self._snapshots = snapshots
        self._config = config or get_operations_config()
        self._locks_dir = Path(locks_dir) if locks_dir is not None else None

    # -------------------------------------------------------------------------
    # Locks
    # -------------------------------------------------------------------------

    def _resolved_locks_dir(self) -> Optional[Path]:
        if self._locks_dir is not None:
            return self._locks_dir
        if self._workspace is not None:
            try:
                return Path(self._workspace.locks_dir)
            except AttributeError:
                return None
        return None

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def list_collections(self, *, user: Any) -> list[str]:
        _check_permission(user, "view_published_content")
        try:
            return self._router.list_collections()
        except Exception as exc:
            from cauldron_content.router import RouterError
            if isinstance(exc, RouterError):
                raise
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

        # Item 3: workspace is mandatory for proposals — refuse fast, no side effects.
        if self._workspace is None:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "workspace.unavailable",
                    "Workspace is required for content proposals.",
                ),
            )

        cfg = self._config
        if not isinstance(operations, list) or len(operations) == 0:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    code="operations.empty",
                    message="At least one operation is required.",
                ),
            )
        if len(operations) > cfg.max_operations_per_change_set:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    code="operations.too_many",
                    message=f"Too many operations: maximum is {cfg.max_operations_per_change_set}.",
                ),
            )

        # Item 2: validate operation dict shape, resolve providers, and enforce
        # a single authoritative provider per proposal.
        providers_seen: set[str] = set()
        for i, op_data in enumerate(operations):
            if not isinstance(op_data, dict):
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.invalid_operation",
                        f"Operation {i} must be a JSON object.",
                    ),
                )
            try:
                routed = self._router.resolve_provider(op_data.get("collection", ""))
            except Exception as exc:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.unroutable_collection",
                        f"Cannot route collection "
                        f"{op_data.get('collection', '')!r}: {str(exc)[:120]}",
                    ),
                )
            providers_seen.add(routed)

        if len(providers_seen) > 1:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.mixed_providers_not_supported",
                    "A change request may only target one provider. "
                    "Split operations by provider.",
                ),
            )

        # Authoritative provider is whatever routing resolved to.
        routed_provider = next(iter(providers_seen))

        # If the caller asserted a provider, it must match routing.
        if provider_name and provider_name != routed_provider:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.provider_mismatch",
                    f"Caller asserted {provider_name!r} but routing resolved to "
                    f"{routed_provider!r}.",
                ),
            )
        authoritative_provider = routed_provider

        # Reject the force field and invalid status before any workspace or DB writes.
        from cauldron_content.contracts import ContentStatus as _CS
        for op_data in operations:
            if "force" in op_data:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.force_not_allowed",
                        "The 'force' field is not permitted in public proposals.",
                    ),
                )
            _status_str = op_data.get("status", "draft")
            try:
                _CS(_status_str)
            except ValueError:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.invalid_status",
                        f"Invalid status: {_status_str!r}. Valid values are 'draft' and 'published'.",
                    ),
                )

        # Build the ContentChangeSet up-front so we can compute the canonical
        # hash independent of caller-dict key ordering.
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
            ContentStatus,
        )

        ops: list = []
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
            status = ContentStatus(op_data.get("status", "draft"))
            ops.append(ContentOperation(
                kind=kind,
                # Item 2: force the authoritative provider on every op.
                provider=authoritative_provider,
                collection=op_data.get("collection", ""),
                item_id=op_data.get("item_id", ""),
                slug=op_data.get("slug", ""),
                expected_hash=op_data.get("expected_hash", ""),
                data=op_data.get("data", {}),
                body=op_data.get("body", ""),
                schema=op_data.get("schema", ""),
                status=status,
                force=False,
            ))

        request_id = str(uuid.uuid4())
        cs_id = str(uuid.uuid4())
        changeset = ContentChangeSet(
            id=cs_id,
            operations=tuple(ops),
            author=str(user.get_username()) if hasattr(user, "get_username") else str(user),
            description=description,
        )

        # Item 1: canonical hash on the built changeset, not caller dict.
        payload_hash = _compute_canonical_changeset_hash(changeset)

        # Idempotency check — scoped to (creator, key). Uses canonical hash.
        if idempotency_key:
            user_pk = getattr(user, "pk", None)
            qs = ContentChangeRequest.objects.filter(idempotency_key=idempotency_key)
            if user_pk is not None:
                qs = qs.filter(created_by_id=user_pk)
            existing = qs.first()
            if existing is not None:
                if existing.payload_hash and existing.payload_hash != payload_hash:
                    return ChangeRequestResult(
                        ok=False,
                        error=OperationError(
                            "idempotency.payload_mismatch",
                            "Idempotency key reused with a different payload.",
                        ),
                    )
                return ChangeRequestResult(
                    ok=True,
                    request_id=existing.request_id,
                    lifecycle_state=existing.lifecycle_state,
                    request_version=existing.request_version,
                    meta={"idempotent": True},
                )

        # Persist to workspace first, then to SQL. On integrity error at SQL
        # insert (concurrent idempotency-key insert), clean up the orphan and
        # return the winning record.
        try:
            self._workspace.create(changeset)
        except Exception as exc:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    code="workspace.create_failed",
                    message="Failed to create workspace change set.",
                    details=(str(exc)[:200],),
                ),
            )

        # Item 13: concurrent-insert race — wrap SQL create in a savepoint and
        # handle the (creator, idempotency_key) UniqueConstraint gracefully.
        try:
            with transaction.atomic():
                cr = ContentChangeRequest.objects.create(
                    request_id=request_id,
                    workspace_changeset_id=cs_id,
                    provider_name=authoritative_provider,
                    lifecycle_state=LifecycleState.PROPOSED.value,
                    payload_hash=payload_hash,
                    idempotency_key=idempotency_key,
                    created_by=user if (hasattr(user, "pk") and user.pk is not None) else None,
                )
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.PROPOSAL_CREATED,
                    actor=user,
                    resulting_state=LifecycleState.PROPOSED.value,
                    provider=authoritative_provider,
                    correlation_id=request_id,
                )
        except IntegrityError:
            # Someone else won the concurrent insert. Clean up our workspace
            # orphan (never the winner's) and re-query the winner.
            try:
                if hasattr(self._workspace, "cleanup_orphan"):
                    self._workspace.cleanup_orphan(cs_id)
            except Exception:
                pass
            if not idempotency_key:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "idempotency.create_conflict",
                        "Concurrent create conflict; retry with an idempotency key.",
                    ),
                )
            user_pk = getattr(user, "pk", None)
            qs = ContentChangeRequest.objects.filter(idempotency_key=idempotency_key)
            if user_pk is not None:
                qs = qs.filter(created_by_id=user_pk)
            winner = qs.first()
            if winner is None:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "idempotency.create_conflict",
                        "Concurrent create conflict and winning record not found.",
                    ),
                )
            if winner.payload_hash and winner.payload_hash != payload_hash:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "idempotency.payload_mismatch",
                        "Idempotency key reused with a different payload.",
                    ),
                )
            return ChangeRequestResult(
                ok=True,
                request_id=winner.request_id,
                lifecycle_state=winner.lifecycle_state,
                request_version=winner.request_version,
                meta={"idempotent": True},
            )

        return ChangeRequestResult(
            ok=True,
            request_id=request_id,
            lifecycle_state=LifecycleState.PROPOSED.value,
            request_version=1,
        )

    def get_change_request(self, request_id: str, *, user: Any) -> Optional[ChangeRequestDetail]:
        _check_permission(user, "view_content_change_requests")
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
        _check_permission(user, "view_content_change_requests")
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
        version_err = _require_positive_version(expected_version)
        if version_err:
            return version_err
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError("not_found", f"Change request {request_id!r} not found."),
                )

            if cr.request_version != expected_version:
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

            # Workspace must be present for validation — fail closed otherwise.
            if self._workspace is None:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "workspace.unavailable",
                        "Workspace is not configured; cannot validate proposal.",
                    ),
                )
            changeset, load_err = _load_workspace_changeset_with_integrity(
                self._workspace, cr.workspace_changeset_id, cr.payload_hash,
            )
            if load_err is not None:
                # Audit the mismatch and bail without state mutation.
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": load_err.code,
                        "error_summary": load_err.message[:200],
                    },
                )
                return ChangeRequestResult(ok=False, error=load_err)

            if not changeset.operations:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "validation.empty_operations",
                        "Proposal contains no operations.",
                    ),
                )

            validation_issues: list[dict] = []
            providers_seen: set[str] = set()
            for op in changeset.operations:
                if not op.collection:
                    validation_issues.append({"code": "missing_collection", "item_id": op.item_id})
                if not op.item_id:
                    validation_issues.append({"code": "missing_item_id", "collection": op.collection})
                try:
                    provider = self._router.resolve_provider(op.collection)
                    providers_seen.add(provider)
                except Exception as exc:
                    validation_issues.append({
                        "code": "routing_error",
                        "collection": op.collection,
                        "detail": str(exc)[:100],
                    })

            if len(providers_seen) > 1:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.mixed_providers_not_supported",
                        "Mixed providers not supported in a single change request.",
                    ),
                )

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

            # Repository validation: call repo.validate() per operation via the routed repository.
            repo_issues: list[dict] = []
            for op in changeset.operations:
                kind_value = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
                _coll = op.collection
                _item_id = op.item_id
                try:
                    _prov = self._router.resolve_provider(_coll)
                    _repo = self._router.get_repo(_prov)
                except Exception as exc:
                    repo_issues.append({"code": "routing_error", "collection": _coll, "item_id": _item_id, "detail": str(exc)[:100]})
                    continue

                if kind_value == "create":
                    try:
                        from cauldron_content.contracts import ContentItem
                        from cauldron_content.hashing import compute_content_hash
                        _slug = op.slug or _item_id
                        _status_val = op.status.value if hasattr(op.status, "value") else str(op.status)
                        _h = compute_content_hash(item_id=_item_id, collection=_coll, slug=_slug, status=_status_val, schema=op.schema, data=dict(op.data), body=op.body)
                        _candidate = ContentItem(id=_item_id, collection=_coll, slug=_slug, status=op.status, schema=op.schema, data=dict(op.data), body=op.body, hash=_h, provider=_prov)
                        _vr = _repo.validate(_candidate)
                        if not _vr.valid:
                            for _issue in _vr.issues:
                                repo_issues.append({"code": _issue.code, "collection": _coll, "item_id": _item_id, "message": _issue.message})
                    except Exception as exc:
                        repo_issues.append({"code": "validation_error", "collection": _coll, "item_id": _item_id, "detail": str(exc)[:100]})

                elif kind_value == "update":
                    if not op.expected_hash:
                        repo_issues.append({"code": "validation.update_requires_expected_hash", "collection": _coll, "item_id": _item_id})
                        continue
                    try:
                        _current = self._router.get_by_id(_item_id, _coll, include_drafts=True)
                    except Exception as exc:
                        repo_issues.append({"code": "routing_error", "collection": _coll, "item_id": _item_id, "detail": str(exc)[:100]})
                        continue
                    if _current is None:
                        repo_issues.append({"code": "validation.item_not_found", "collection": _coll, "item_id": _item_id})
                        continue
                    if _current.hash != op.expected_hash:
                        repo_issues.append({"code": "validation.stale_hash", "collection": _coll, "item_id": _item_id})
                        continue
                    try:
                        from cauldron_content.contracts import ContentItem
                        from cauldron_content.hashing import compute_content_hash
                        _merged = {**dict(_current.data), **dict(op.data)}
                        _slug = op.slug or _current.slug
                        _body = op.body if op.body else _current.body
                        _schema = op.schema if op.schema else _current.schema
                        _status = op.status
                        _status_val = _status.value if hasattr(_status, "value") else str(_status)
                        _h = compute_content_hash(item_id=_item_id, collection=_coll, slug=_slug, status=_status_val, schema=_schema, data=_merged, body=_body)
                        _candidate = ContentItem(id=_item_id, collection=_coll, slug=_slug, status=_status, schema=_schema, data=_merged, body=_body, hash=_h, provider=_prov)
                        _vr = _repo.validate(_candidate)
                        if not _vr.valid:
                            for _issue in _vr.issues:
                                repo_issues.append({"code": _issue.code, "collection": _coll, "item_id": _item_id, "message": _issue.message})
                    except Exception as exc:
                        repo_issues.append({"code": "validation_error", "collection": _coll, "item_id": _item_id, "detail": str(exc)[:100]})

                elif kind_value == "delete":
                    if not op.expected_hash:
                        repo_issues.append({"code": "validation.delete_requires_expected_hash", "collection": _coll, "item_id": _item_id})
                        continue
                    try:
                        _current = self._router.get_by_id(_item_id, _coll, include_drafts=True)
                    except Exception as exc:
                        repo_issues.append({"code": "routing_error", "collection": _coll, "item_id": _item_id, "detail": str(exc)[:100]})
                        continue
                    if _current is None:
                        repo_issues.append({"code": "validation.item_not_found", "collection": _coll, "item_id": _item_id})
                        continue
                    if _current.hash != op.expected_hash:
                        repo_issues.append({"code": "validation.stale_hash", "collection": _coll, "item_id": _item_id})

            if repo_issues:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={"issues": repo_issues[:10]},
                )
                cr.last_error_code = "validation.failed"
                cr.last_error_summary = f"{len(repo_issues)} validation issue(s)."
                cr.save(update_fields=["last_error_code", "last_error_summary", "updated_at"])
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "validation.failed",
                        f"Validation failed: {len(repo_issues)} issue(s).",
                        details=tuple(str(i) for i in repo_issues[:5]),
                    ),
                )

            cr.lifecycle_state = LifecycleState.VALIDATED.value
            cr.request_version += 1
            cr.validated_by = user if (hasattr(user, "pk") and user.pk is not None) else None
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

        # Item 14: mirror the workspace manifest state (best-effort).
        try:
            from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
            _safe_workspace_transition(
                self._workspace, cr.workspace_changeset_id, _WsState.VALIDATED,
            )
        except Exception:
            pass

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
        version_err = _require_positive_version(expected_version)
        if version_err:
            return version_err
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

            if cr.request_version != expected_version:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "conflict.version",
                        f"Version conflict: expected {expected_version}, got {cr.request_version}.",
                    ),
                )

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
            cr.approved_by = user if (hasattr(user, "pk") and user.pk is not None) else None
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
        version_err = _require_positive_version(expected_version)
        if version_err:
            return version_err
        correlation_id = str(uuid.uuid4())

        with transaction.atomic():
            try:
                cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
            except ContentChangeRequest.DoesNotExist:
                return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

            if cr.request_version != expected_version:
                return ChangeRequestResult(ok=False, error=OperationError("conflict.version", "Version conflict."))

            current_state = cr.current_state
            try:
                assert_transition(current_state, LifecycleState.REJECTED)
            except LifecycleError as exc:
                return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

            cr.lifecycle_state = LifecycleState.REJECTED.value
            cr.request_version += 1
            cr.rejected_by = user if (hasattr(user, "pk") and user.pk is not None) else None
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

        # Item 14: mirror rejection to the workspace manifest.
        try:
            from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
            _safe_workspace_transition(
                self._workspace, cr.workspace_changeset_id, _WsState.REJECTED,
            )
        except Exception:
            pass

        return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.REJECTED.value, request_version=cr.request_version)

    def apply_change_request(
        self,
        request_id: str,
        *,
        user: Any,
        expected_version: int = 0,
    ) -> ChangeRequestResult:
        _check_permission(user, "apply_content_changes")
        version_err = _require_positive_version(expected_version)
        if version_err:
            return version_err
        cfg = self._config
        correlation_id = str(uuid.uuid4())

        # Idempotency: already applied
        try:
            cr_check = ContentChangeRequest.objects.get(request_id=request_id)
            if cr_check.current_state == LifecycleState.APPLIED:
                return ChangeRequestResult(
                    ok=True,
                    request_id=request_id,
                    lifecycle_state=LifecycleState.APPLIED.value,
                    request_version=cr_check.request_version,
                    meta={"idempotent": True},
                )
        except ContentChangeRequest.DoesNotExist:
            return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

        if self._workspace is None:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "workspace.unavailable",
                    "Workspace is not configured; cannot apply.",
                ),
            )

        # Load changeset with integrity check (Item 1). Also refuses persisted
        # force=True operations.
        changeset, load_err = _load_workspace_changeset_with_integrity(
            self._workspace, cr_check.workspace_changeset_id, cr_check.payload_hash,
        )
        if load_err is not None:
            try:
                cr_audit = ContentChangeRequest.objects.get(request_id=request_id)
                append_audit_event(
                    change_request=cr_audit,
                    event_type=AuditEventType.APPLICATION_FAILED,
                    actor=user,
                    previous_state=cr_audit.lifecycle_state,
                    resulting_state=cr_audit.lifecycle_state,
                    provider=cr_audit.provider_name,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": load_err.code,
                        "error_summary": load_err.message[:200],
                    },
                )
            except Exception:
                pass
            return ChangeRequestResult(ok=False, error=load_err)

        providers_seen: set[str] = set()
        for op in changeset.operations:
            try:
                providers_seen.add(self._router.resolve_provider(op.collection))
            except Exception:
                pass
        if len(providers_seen) > 1:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.mixed_providers_not_supported",
                    "Mixed providers not supported in a single change request.",
                ),
            )
        # Authoritative provider is what SQL stored at proposal creation.
        provider_name = cr_check.provider_name

        locks_dir = self._resolved_locks_dir()
        if locks_dir is None:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "workspace.unavailable",
                    "Workspace locks directory is unavailable; cannot apply safely.",
                ),
            )

        timeout = float(cfg.lock_timeout)

        with request_lock(request_id, locks_dir, timeout=timeout):
            with provider_lock(provider_name, locks_dir, timeout=timeout):
                # Step 1: Verify version and state under row lock (read-only — no save).
                with transaction.atomic():
                    try:
                        cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
                    except ContentChangeRequest.DoesNotExist:
                        return ChangeRequestResult(ok=False, error=OperationError("not_found", "Not found."))

                    if cr.request_version != expected_version:
                        return ChangeRequestResult(
                            ok=False,
                            error=OperationError(
                                "conflict.version",
                                f"Version conflict: expected {expected_version}, got {cr.request_version}.",
                            ),
                        )

                    current_state = cr.current_state

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
                    # Row lock released here — no mutation yet.

                # Step 2: Prepare rollback artifact BEFORE marking applying and BEFORE mutation.
                adapter = get_adapter(provider_name)
                if adapter is not None and adapter.supports_rollback:
                    try:
                        adapter.prepare(cr.workspace_changeset_id, changeset)
                    except Exception as exc:
                        _prep_err = str(exc)[:500]
                        with transaction.atomic():
                            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                            cr2.lifecycle_state = LifecycleState.APPLY_FAILED.value
                            cr2.request_version += 1
                            cr2.last_error_code = "application.rollback_artifact_failed"
                            cr2.last_error_summary = _prep_err
                            cr2.save(update_fields=["lifecycle_state", "request_version", "last_error_code", "last_error_summary", "updated_at"])
                            append_audit_event(
                                change_request=cr2,
                                event_type=AuditEventType.APPLICATION_FAILED,
                                actor=user,
                                previous_state=current_state.value,
                                resulting_state=LifecycleState.APPLY_FAILED.value,
                                provider=cr.provider_name,
                                correlation_id=correlation_id,
                                detail={"error_code": "application.rollback_artifact_failed", "error_summary": _prep_err},
                            )
                        return ChangeRequestResult(
                            ok=False,
                            error=OperationError("application.rollback_artifact_failed", "Failed to prepare rollback artifact; apply aborted."),
                            request_id=request_id,
                            lifecycle_state=LifecycleState.APPLY_FAILED.value,
                        )

                # Step 3: Mark applying now that the snapshot is ready.
                with transaction.atomic():
                    cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
                    cr.lifecycle_state = LifecycleState.APPLYING.value
                    cr.request_version += 1
                    cr.applied_by = user if (hasattr(user, "pk") and user.pk is not None) else None
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
                # Applying state is now durable.

                # Step 4: Apply through router.
                try:
                    apply_result = self._router.apply(changeset)
                except Exception as exc:
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
                    try:
                        from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
                        _safe_workspace_transition(
                            self._workspace, cr.workspace_changeset_id, _WsState.FAILED,
                        )
                    except Exception:
                        pass
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

                # Steps 5+6: Record post-application state and persist result artifact.
                # Any failure after a successful mutation → reconciliation_required.
                _recon_needed = False
                _recon_error = ""

                if adapter is not None and adapter.supports_rollback:
                    try:
                        adapter.record_applied(cr.workspace_changeset_id)
                    except Exception as exc:
                        _recon_needed = True
                        _recon_error = str(exc)[:500]

                _result_meta: dict = {}
                if not _recon_needed:
                    _result_meta = {
                        "applied_count": len(apply_result.applied),
                        "correlation_id": correlation_id,
                    }
                    try:
                        self._workspace.save_application_result(cr.workspace_changeset_id, _result_meta)
                    except Exception as exc:
                        _recon_needed = True
                        _recon_error = str(exc)[:500]

                if _recon_needed:
                    with transaction.atomic():
                        cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                        cr2.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
                        cr2.request_version += 1
                        cr2.last_error_code = "application.reconciliation_required"
                        cr2.last_error_summary = _recon_error
                        cr2.save(update_fields=["lifecycle_state", "request_version", "last_error_code", "last_error_summary", "updated_at"])
                        append_audit_event(
                            change_request=cr2,
                            event_type=AuditEventType.APPLICATION_FAILED,
                            actor=user,
                            previous_state=LifecycleState.APPLYING.value,
                            resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                            provider=cr.provider_name,
                            correlation_id=correlation_id,
                            detail={"error_code": "application.reconciliation_required", "error_summary": _recon_error},
                        )
                    return ChangeRequestResult(
                        ok=False,
                        error=OperationError("application.reconciliation_required", "Post-application persistence failed; reconciliation required."),
                        request_id=request_id,
                        lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                    )

                # Step 7: Mark applied durably.
                with transaction.atomic():
                    cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                    cr2.lifecycle_state = LifecycleState.APPLIED.value
                    cr2.request_version += 1
                    cr2.applied_at = datetime.now(timezone.utc)
                    cr2.application_result_meta = {
                        "applied_count": _result_meta["applied_count"],
                        "correlation_id": correlation_id,
                    }
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
                        detail={"applied_count": _result_meta["applied_count"]},
                    )

                # Item 14: mirror apply success in workspace manifest.
                try:
                    from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
                    _safe_workspace_transition(
                        self._workspace, cr.workspace_changeset_id, _WsState.APPLIED,
                    )
                except Exception:
                    pass

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
        version_err = _require_positive_version(expected_version)
        if version_err:
            return version_err
        # Item 6: enforce superuser check before touching any state.
        if force and not getattr(user, "is_superuser", False):
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "rollback.force_requires_superuser",
                    "Forced rollback requires superuser privileges.",
                ),
            )
        correlation_id = str(uuid.uuid4())

        try:
            cr_check = ContentChangeRequest.objects.get(request_id=request_id)
            if cr_check.current_state == LifecycleState.ROLLED_BACK:
                return ChangeRequestResult(
                    ok=True,
                    request_id=request_id,
                    lifecycle_state=LifecycleState.ROLLED_BACK.value,
                    request_version=cr_check.request_version,
                    meta={"idempotent": True},
                )
        except ContentChangeRequest.DoesNotExist:
            return ChangeRequestResult(ok=False, error=OperationError("not_found", f"Not found: {request_id!r}"))

        provider_name = cr_check.provider_name
        adapter = get_adapter(provider_name)
        if adapter is None or not adapter.supports_rollback:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "rollback.not_supported",
                    f"Provider {provider_name!r} does not support rollback.",
                ),
            )
        if not adapter.has_rollback_artifact(cr_check.workspace_changeset_id):
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "rollback.no_artifact",
                    "No rollback artifact found for this change request.",
                ),
            )

        locks_dir = self._resolved_locks_dir()
        if locks_dir is None:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "workspace.unavailable",
                    "Workspace locks directory is unavailable; cannot roll back safely.",
                ),
            )
        timeout = float(self._config.lock_timeout)

        with request_lock(request_id, locks_dir, timeout=timeout):
            with provider_lock(provider_name, locks_dir, timeout=timeout):
                with transaction.atomic():
                    try:
                        cr = ContentChangeRequest.objects.select_for_update().get(request_id=request_id)
                    except ContentChangeRequest.DoesNotExist:
                        return ChangeRequestResult(ok=False, error=OperationError("not_found", "Not found."))

                    if cr.request_version != expected_version:
                        return ChangeRequestResult(
                            ok=False,
                            error=OperationError(
                                "conflict.version",
                                f"Version conflict: expected {expected_version}, got {cr.request_version}.",
                            ),
                        )

                    current_state = cr.current_state
                    try:
                        assert_transition(current_state, LifecycleState.ROLLING_BACK)
                    except LifecycleError as exc:
                        return ChangeRequestResult(ok=False, error=OperationError(exc.code, exc.message))

                    cr.lifecycle_state = LifecycleState.ROLLING_BACK.value
                    cr.request_version += 1
                    cr.rolled_back_by = user if (hasattr(user, "pk") and user.pk is not None) else None
                    cr.save(update_fields=["lifecycle_state", "request_version", "rolled_back_by", "updated_at"])

                    detail: dict[str, Any] = {}
                    if force:
                        detail["forced"] = True
                        detail["forced_by"] = (
                            user.get_username() if hasattr(user, "get_username") else str(user)
                        )
                    append_audit_event(
                        change_request=cr,
                        event_type=AuditEventType.ROLLBACK_STARTED,
                        actor=user,
                        previous_state=current_state.value,
                        resulting_state=LifecycleState.ROLLING_BACK.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail=detail,
                    )

                # Perform rollback via adapter (outside DB transaction)
                rollback_ok = False
                rollback_error = ""
                rollback_error_code = "rollback.failed"
                try:
                    adapter.rollback(
                        cr.workspace_changeset_id,
                        force=force,
                        is_superuser=bool(getattr(user, "is_superuser", False)),
                    )
                    rollback_ok = True
                except PermissionError as exc:
                    rollback_error = str(exc)
                    rollback_error_code = "rollback.force_requires_superuser"
                except Exception as exc:
                    # Detect the "post state unavailable" branch from Item 6.
                    exc_type_name = type(exc).__name__
                    if exc_type_name == "RollbackPostStateUnavailable":
                        rollback_error_code = "rollback.post_state_unavailable"
                    rollback_error = str(exc)[:500]

                # Item 9: verify state, then persist result. Any failure after
                # a successful rollback → reconciliation_required.
                verification_failed = False
                verification_reason = ""
                persistence_failed = False
                persistence_reason = ""
                if rollback_ok:
                    try:
                        vr = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
                        if getattr(vr, "status", "") != "verified":
                            verification_failed = True
                            verification_reason = getattr(vr, "reason", "") or "verification failed"
                    except Exception as exc:
                        # An adapter without the verify method is acceptable.
                        # A raised exception is not.
                        if not isinstance(exc, AttributeError):
                            verification_failed = True
                            verification_reason = str(exc)[:200]

                    if not verification_failed:
                        try:
                            self._workspace.save_rollback_result(
                                cr.workspace_changeset_id,
                                {"correlation_id": correlation_id},
                            )
                        except Exception as exc:
                            persistence_failed = True
                            persistence_reason = str(exc)[:200]

                with transaction.atomic():
                    cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                    if rollback_ok and not verification_failed and not persistence_failed:
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
                    elif rollback_ok and (verification_failed or persistence_failed):
                        # Post-mutation reconciliation branch (Item 9).
                        code = (
                            "rollback.verification_failed" if verification_failed
                            else "rollback.result_persistence_failed"
                        )
                        summary = verification_reason if verification_failed else persistence_reason
                        cr2.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
                        cr2.last_error_code = code
                        cr2.last_error_summary = summary
                        cr2.request_version += 1
                        cr2.save(update_fields=["lifecycle_state", "last_error_code", "last_error_summary", "request_version", "updated_at"])
                        append_audit_event(
                            change_request=cr2,
                            event_type=AuditEventType.RECONCILIATION_FAILED,
                            actor=user,
                            previous_state=LifecycleState.ROLLING_BACK.value,
                            resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                            provider=cr.provider_name,
                            correlation_id=correlation_id,
                            detail={"error_code": code, "error_summary": summary},
                        )
                    else:
                        cr2.lifecycle_state = LifecycleState.ROLLBACK_FAILED.value
                        cr2.last_error_code = rollback_error_code
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
                            detail={"error_summary": rollback_error, "error_code": rollback_error_code},
                        )

        if rollback_ok and not verification_failed and not persistence_failed:
            return ChangeRequestResult(ok=True, request_id=request_id, lifecycle_state=LifecycleState.ROLLED_BACK.value, request_version=cr2.request_version)
        if rollback_ok and (verification_failed or persistence_failed):
            code = (
                "rollback.verification_failed" if verification_failed
                else "rollback.result_persistence_failed"
            )
            summary = verification_reason if verification_failed else persistence_reason
            return ChangeRequestResult(
                ok=False,
                error=OperationError(code, summary or code),
                request_id=request_id,
                lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value,
            )
        return ChangeRequestResult(ok=False, error=OperationError(rollback_error_code, rollback_error), request_id=request_id, lifecycle_state=LifecycleState.ROLLBACK_FAILED.value)

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
        _check_permission(user, "view_content_change_requests")
        try:
            cr = ContentChangeRequest.objects.get(request_id=request_id)
        except ContentChangeRequest.DoesNotExist:
            return None

        if self._workspace is None:
            return None
        # Item 1: preview must operate on integrity-verified payload.
        changeset, load_err = _load_workspace_changeset_with_integrity(
            self._workspace, cr.workspace_changeset_id, cr.payload_hash,
        )
        if load_err is not None or changeset is None:
            return None

        previews = []
        for op in changeset.operations:
            item_id = op.item_id
            collection = op.collection
            current_item = None
            try:
                can_see_drafts = getattr(user, "is_superuser", False) or user.has_perm("cauldron_content_operations.view_draft_content")
                current_item = self._router.get_by_id(item_id, collection, include_drafts=can_see_drafts)
            except Exception:
                pass

            from cauldron_content.hashing import compute_content_hash
            proposed_data = dict(op.data)
            proposed_body = op.body
            proposed_slug = op.slug or item_id
            status_str = op.status.value if hasattr(op.status, "value") else str(op.status)

            try:
                proposed_hash = compute_content_hash(
                    item_id=item_id,
                    collection=collection,
                    slug=proposed_slug,
                    status=status_str,
                    schema=op.schema,
                    data=proposed_data,
                    body=proposed_body,
                )
            except Exception:
                proposed_hash = ""

            import html
            current_body = current_item.body if current_item else ""
            current_data = dict(current_item.data) if current_item else {}
            current_hash = current_item.hash if current_item else ""
            has_conflict = bool(op.expected_hash and current_item and op.expected_hash != current_hash)

            diff_lines = []
            if current_body != proposed_body:
                diff_lines.append(f"Body changed ({len(current_body)} -> {len(proposed_body)} chars)")
            if current_data != proposed_data:
                diff_lines.append("Structured data changed")
            diff_summary = html.escape("; ".join(diff_lines) if diff_lines else "No text changes")

            kind_str = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            previews.append(OperationPreview(
                operation_type=kind_str,
                collection=collection,
                item_id=item_id,
                provider=op.provider or cr.provider_name,
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
        _check_permission(user, "apply_content_changes")

        transitional = list(
            ContentChangeRequest.objects.filter(
                lifecycle_state__in=[
                    LifecycleState.APPLYING.value,
                    LifecycleState.ROLLING_BACK.value,
                    LifecycleState.RECONCILIATION_REQUIRED.value,
                ]
            )
        )

        locks_dir = self._resolved_locks_dir()
        timeout = float(self._config.lock_timeout)

        results: list[dict[str, Any]] = []
        for cr in transitional:
            entry: dict[str, Any] = {
                "request_id": cr.request_id,
                "current_state": cr.lifecycle_state,
                "action": None,
                "reason": "",
                "applied": False,
            }

            def _lock_ctx():
                if locks_dir is not None:
                    return request_lock(cr.request_id, locks_dir, timeout=timeout)
                return nullcontext()

            if dry_run:
                # Read-only inspection: no locks, no mutations.
                self._reconcile_inspect(cr, entry)
                results.append(entry)
                continue

            with _lock_ctx():
                self._reconcile_one(cr, user, entry)
            results.append(entry)

        return results

    # -------------------------------------------------------------------------
    # Reconciliation helpers
    # -------------------------------------------------------------------------

    def _reconcile_inspect(self, cr: ContentChangeRequest, entry: dict) -> None:
        """Populate ``entry`` without mutating DB, workspace, or providers."""
        adapter = get_adapter(cr.provider_name) if cr.provider_name else None
        app_result = (
            self._workspace.load_application_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )
        rb_result = (
            self._workspace.load_rollback_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )
        state = cr.lifecycle_state
        if state == LifecycleState.APPLYING.value:
            if app_result and app_result.get("result_type") == "applied":
                if adapter is not None:
                    try:
                        vr = adapter.verify_applied_state(cr.workspace_changeset_id)
                    except Exception as exc:
                        entry["action"] = "leave_ambiguous"
                        entry["reason"] = f"Verification error: {str(exc)[:120]}"
                        return
                    if vr.status != "verified":
                        entry["action"] = "leave_ambiguous"
                        entry["reason"] = f"Applied state {vr.status}: {vr.reason[:120]}"
                        return
                entry["action"] = "would_finalize_applied"
                entry["reason"] = "Application result present and provider state verified."
            else:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = "No confirmed application result found."
        elif state == LifecycleState.ROLLING_BACK.value:
            if rb_result and rb_result.get("result_type") == "rolled_back":
                if adapter is not None:
                    try:
                        vr = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
                    except Exception as exc:
                        entry["action"] = "leave_ambiguous"
                        entry["reason"] = f"Verification error: {str(exc)[:120]}"
                        return
                    if vr.status != "verified":
                        entry["action"] = "leave_ambiguous"
                        entry["reason"] = f"Rollback state {vr.status}: {vr.reason[:120]}"
                        return
                entry["action"] = "would_finalize_rolled_back"
                entry["reason"] = "Rollback result present and provider state verified."
            else:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = "No confirmed rollback result found."
        elif state == LifecycleState.RECONCILIATION_REQUIRED.value:
            # Try to disambiguate: prefer proven applied, then proven rolled_back.
            if adapter is not None:
                try:
                    vapp = adapter.verify_applied_state(cr.workspace_changeset_id)
                    vrb = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
                except Exception:
                    vapp = None
                    vrb = None
                if vapp is not None and vapp.status == "verified" and app_result:
                    entry["action"] = "would_finalize_applied"
                    entry["reason"] = "Verified applied state via provider."
                    return
                if vrb is not None and vrb.status == "verified" and rb_result:
                    entry["action"] = "would_finalize_rolled_back"
                    entry["reason"] = "Verified rolled-back state via provider."
                    return
            entry["action"] = "requires_manual_review"
            entry["reason"] = "State remains ambiguous — needs manual review."
        else:
            entry["action"] = "requires_manual_review"
            entry["reason"] = f"State {state!r} requires manual review."

    def _reconcile_one(
        self,
        cr: ContentChangeRequest,
        user: Any,
        entry: dict,
    ) -> None:
        adapter = get_adapter(cr.provider_name) if cr.provider_name else None

        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            append_audit_event(
                change_request=cr2,
                event_type=AuditEventType.RECONCILIATION_STARTED,
                actor=user,
                previous_state=cr2.lifecycle_state,
                resulting_state=cr2.lifecycle_state,
                provider=cr2.provider_name,
            )

        state = cr.lifecycle_state
        app_result = (
            self._workspace.load_application_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )
        rb_result = (
            self._workspace.load_rollback_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )

        if state == LifecycleState.APPLYING.value:
            typed_applied = bool(app_result and app_result.get("result_type") == "applied")
            verified = False
            reason = ""
            if typed_applied and adapter is not None:
                try:
                    vr = adapter.verify_applied_state(cr.workspace_changeset_id)
                    verified = vr.status == "verified"
                    reason = vr.reason
                except Exception as exc:
                    verified = False
                    reason = str(exc)[:120]
            if typed_applied and verified:
                entry["action"] = "finalize_applied"
                self._finalize_applied(cr, user, app_result)
                entry["applied"] = True
            else:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = (
                    "No confirmed application result found."
                    if not typed_applied else
                    f"Provider verification failed: {reason[:120]}"
                )
                self._mark_reconciliation_required(cr, user)
        elif state == LifecycleState.ROLLING_BACK.value:
            # NEVER finalize rolling_back as applied.
            typed_rb = bool(rb_result and rb_result.get("result_type") == "rolled_back")
            verified = False
            reason = ""
            if typed_rb and adapter is not None:
                try:
                    vr = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
                    verified = vr.status == "verified"
                    reason = vr.reason
                except Exception as exc:
                    verified = False
                    reason = str(exc)[:120]
            if typed_rb and verified:
                # Guard: never rewrite rolling_back → applied.
                assert cr.lifecycle_state != LifecycleState.APPLIED.value
                entry["action"] = "finalize_rolled_back"
                self._finalize_rolled_back(cr, user, rb_result)
                entry["applied"] = True
            else:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = (
                    "No confirmed rollback result found."
                    if not typed_rb else
                    f"Provider verification failed: {reason[:120]}"
                )
                self._mark_reconciliation_required(cr, user)
        elif state == LifecycleState.RECONCILIATION_REQUIRED.value:
            # Attempt safe disambiguation using provider verification.
            if adapter is not None:
                try:
                    vapp = adapter.verify_applied_state(cr.workspace_changeset_id)
                except Exception:
                    vapp = None
                try:
                    vrb = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
                except Exception:
                    vrb = None
                if vapp is not None and vapp.status == "verified" and app_result:
                    entry["action"] = "finalize_applied"
                    self._finalize_applied(cr, user, app_result)
                    entry["applied"] = True
                    return
                if vrb is not None and vrb.status == "verified" and rb_result:
                    entry["action"] = "finalize_rolled_back"
                    self._finalize_rolled_back(cr, user, rb_result)
                    entry["applied"] = True
                    return
            entry["action"] = "requires_manual_review"
            entry["reason"] = "State remains ambiguous — needs manual review."
        else:
            entry["action"] = "requires_manual_review"
            entry["reason"] = f"State {state!r} requires manual review."

    def _finalize_applied(
        self,
        cr: ContentChangeRequest,
        user: Any,
        app_result: dict,
    ) -> None:
        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            cr2.lifecycle_state = LifecycleState.APPLIED.value
            cr2.request_version += 1
            cr2.applied_at = cr2.applied_at or datetime.now(timezone.utc)
            cr2.reconciliation_meta = {
                "reconciled": True,
                "source": "application_result",
                "applied_count": app_result.get("applied_count"),
            }
            cr2.save(update_fields=[
                "lifecycle_state", "request_version", "applied_at",
                "reconciliation_meta", "updated_at",
            ])
            append_audit_event(
                change_request=cr2,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                actor=user,
                previous_state=cr.lifecycle_state,
                resulting_state=LifecycleState.APPLIED.value,
                provider=cr.provider_name,
            )

    def _finalize_rolled_back(
        self,
        cr: ContentChangeRequest,
        user: Any,
        rb_result: dict,
    ) -> None:
        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            cr2.lifecycle_state = LifecycleState.ROLLED_BACK.value
            cr2.request_version += 1
            cr2.rolled_back_at = cr2.rolled_back_at or datetime.now(timezone.utc)
            cr2.reconciliation_meta = {
                "reconciled": True,
                "source": "rollback_result",
            }
            cr2.save(update_fields=[
                "lifecycle_state", "request_version", "rolled_back_at",
                "reconciliation_meta", "updated_at",
            ])
            append_audit_event(
                change_request=cr2,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                actor=user,
                previous_state=cr.lifecycle_state,
                resulting_state=LifecycleState.ROLLED_BACK.value,
                provider=cr.provider_name,
            )

    def _mark_reconciliation_required(
        self,
        cr: ContentChangeRequest,
        user: Any,
    ) -> None:
        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            if cr2.lifecycle_state == LifecycleState.RECONCILIATION_REQUIRED.value:
                return
            previous = cr2.lifecycle_state
            cr2.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
            cr2.request_version += 1
            cr2.save(update_fields=["lifecycle_state", "request_version", "updated_at"])
            append_audit_event(
                change_request=cr2,
                event_type=AuditEventType.RECONCILIATION_FAILED,
                actor=user,
                previous_state=previous,
                resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                provider=cr.provider_name,
            )
