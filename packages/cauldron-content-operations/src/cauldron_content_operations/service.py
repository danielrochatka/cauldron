"""ContentOperationService — the single application service for permissioned content mutations."""
from __future__ import annotations

import hashlib
import json
import logging
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


logger = logging.getLogger(__name__)


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


# Item 2: strict protocol requirements for a v2 reversible adapter.
# Providers advertising ``supports_rollback=True`` MUST implement every one
# of these members. Adapters lacking any member are treated as unsupported
# and get ``rollback.not_supported`` — we no longer fall back to legacy
# call shapes.
_REQUIRED_ADAPTER_METHODS = (
    "prepare",
    "record_applied",
    "record_rolled_back",
    "rollback",
    "has_rollback_artifact",
    "verify_applied_state",
    "verify_rolled_back_state",
    "inspect",
)


def _adapter_fully_supports_rollback(adapter: Any) -> bool:
    """Return True iff the adapter advertises rollback support and implements
    every required method.

    Item 2: providers advertising ``supports_rollback=True`` must implement
    every protocol member. We fail closed if any method is missing.
    """
    if adapter is None:
        return False
    if not bool(getattr(adapter, "supports_rollback", False)):
        return False
    for name in _REQUIRED_ADAPTER_METHODS:
        if not callable(getattr(adapter, name, None)):
            return False
    return True


def _is_valid_sha256_hex(value: Any) -> bool:
    """Return True iff ``value`` is a 64-char lowercase-hex string."""
    if not isinstance(value, str):
        return False
    if len(value) != 64:
        return False
    for ch in value:
        if ch not in "0123456789abcdef":
            return False
    return True


def _payload_hash_error() -> OperationError:
    return OperationError(
        "workspace.payload_integrity_unavailable",
        "Persisted workspace payload hash is missing or invalid.",
    )


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

    Item 1: ``expected_hash`` MUST be a valid SHA-256 hex string. Absence or
    invalid form maps to ``workspace.payload_integrity_unavailable`` — we
    fail closed rather than silently skipping the check.
    """
    # Item 1: require a valid SHA-256 hex payload hash before any load.
    if not _is_valid_sha256_hex(expected_hash):
        return None, _payload_hash_error()

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

    if actual_hash != expected_hash:
        return None, OperationError(
            "workspace.payload_integrity_mismatch",
            "Persisted workspace payload does not match the recorded hash.",
        )
    return changeset, None


def _check_provider_routing(
    router: Any,
    changeset: Any,
    expected_provider: str,
) -> Optional[OperationError]:
    """Item 2: verify that every op routes to ``expected_provider``, the
    persisted op provider matches, and the total provider set is exactly
    ``{expected_provider}``.

    Returns an OperationError on drift, else None.
    """
    seen_providers: set[str] = set()
    for op in getattr(changeset, "operations", ()) or ():
        # Router drift
        try:
            routed = router.resolve_provider(op.collection)
        except Exception as exc:
            return OperationError(
                "operations.unroutable_collection",
                f"Cannot route collection {op.collection!r}: {str(exc)[:120]}",
            )
        if routed != expected_provider:
            return OperationError(
                "operations.provider_route_changed",
                f"Route for collection {op.collection!r} resolved to "
                f"{routed!r}, expected {expected_provider!r}.",
            )
        # Persisted op provider must match request provider
        persisted = getattr(op, "provider", "") or ""
        if persisted and persisted != expected_provider:
            return OperationError(
                "operations.provider_mismatch",
                f"Persisted op provider {persisted!r} does not match request "
                f"provider {expected_provider!r}.",
            )
        seen_providers.add(routed)
    if seen_providers and seen_providers != {expected_provider}:
        return OperationError(
            "operations.provider_route_changed",
            f"Routing resolved to providers {sorted(seen_providers)!r}, "
            f"expected exactly {[expected_provider]!r}.",
        )
    return None


def _check_duplicate_targets(changeset: Any) -> Optional[OperationError]:
    """Item 9: reject changesets where multiple ops target the same canonical
    (collection, item_id or slug) tuple.

    We use ``(collection, slug or item_id)`` as the canonical key; this
    matches how :class:`FlatFileReversibleMutationAdapter._canonical_path_for_op`
    resolves paths.
    """
    seen: dict[tuple[str, str], int] = {}
    for i, op in enumerate(getattr(changeset, "operations", ()) or ()):
        coll = op.collection or ""
        # For create/update we prefer slug; for delete slug may be empty.
        slug = op.slug or op.item_id or ""
        key = (coll, slug)
        # Also record item_id-based key for cross-collision detection.
        alt_key = (coll, op.item_id or slug)
        for k in {key, alt_key}:
            if k in seen:
                return OperationError(
                    "operations.duplicate_target",
                    f"Multiple operations target {k[0]!r}/{k[1]!r} "
                    f"(op indexes {seen[k]} and {i}).",
                )
            seen[k] = i
    return None


def _safe_workspace_transition(workspace: Any, cs_id: str, new_state: Any) -> bool:
    """Best-effort workspace state transition, silencing recoverable errors.

    Workspace state is informational for validation/rejection/approval; SQL is
    authoritative. Returns ``True`` on success, ``False`` on failure so callers
    can escalate when they need to (e.g. apply/rollback).

    Item 16: SQL states without a direct workspace equivalent
    (``proposed``, ``applying``, ``applied``, ``rolling_back``, ``rolled_back``,
    ``rollback_failed``, ``reconciliation_required``) are intentionally not
    mirrored back through this helper. The workspace state machine is a
    coarse observability mirror; SQL is authoritative for lifecycle progression.
    """
    if workspace is None:
        return False
    try:
        workspace.transition(cs_id, new_state)
        return True
    except Exception as exc:
        # Do not block SQL lifecycle progression on workspace observability
        # failures. Log at debug so operators can trace.
        logger.debug(
            "workspace transition to %r failed for %s: %s",
            getattr(new_state, "value", new_state), cs_id, exc,
        )
        return False


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


def _record_reconciliation_reason(cr: ContentChangeRequest, code: str, summary: str) -> None:
    """Item 12: attach a bounded reconciliation failure reason to metadata."""
    meta = dict(cr.metadata or {})
    meta["reconciliation_failure_reason"] = {
        "code": code,
        "summary": summary[:500],
    }
    cr.metadata = meta


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

        # Item 13: validate collection and slug identifiers up-front, before
        # any workspace or DB writes. Failures map to
        # ``operations.invalid_collection`` / ``operations.invalid_slug``.
        from ._identifiers import validate_identifier_segment as _vseg

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
            # Item 13: collection segment must be identifier-safe.
            try:
                _vseg(op_data.get("collection", ""), "collection")
            except ValueError as exc:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.invalid_collection",
                        f"Operation {i}: {exc}",
                    ),
                )
            # Item 13: slug segment (when supplied) must also be safe.
            _slug_val = op_data.get("slug", "")
            if _slug_val:
                try:
                    _vseg(_slug_val, "slug")
                except ValueError as exc:
                    return ChangeRequestResult(
                        ok=False,
                        error=OperationError(
                            "operations.invalid_slug",
                            f"Operation {i}: {exc}",
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

        # Item 9: reject duplicate canonical targets at proposal time so we
        # never persist an unresolvable changeset.
        dup_err = _check_duplicate_targets(changeset)
        if dup_err is not None:
            return ChangeRequestResult(ok=False, error=dup_err)

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

        # Item 13/18: concurrent-insert race — wrap SQL create in a savepoint
        # and handle the (creator, idempotency_key) UniqueConstraint gracefully.
        # Any exception AFTER workspace create but BEFORE durable SQL row must
        # clean up the workspace orphan unless a durable ContentChangeRequest
        # for that cs_id already exists.
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
            self._safe_cleanup_orphan(cs_id)
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
        except Exception:
            # Item 18: any unexpected exception (audit failure, DB failure)
            # after workspace create must clean up the orphan, provided no
            # durable ContentChangeRequest exists for this cs_id.
            self._safe_cleanup_orphan(cs_id)
            raise

        return ChangeRequestResult(
            ok=True,
            request_id=request_id,
            lifecycle_state=LifecycleState.PROPOSED.value,
            request_version=1,
        )

    def _safe_cleanup_orphan(self, cs_id: str) -> None:
        """Item 18: clean up a workspace changeset iff no durable SQL record
        exists for that cs_id. Logs but never raises on cleanup failure.
        """
        try:
            if ContentChangeRequest.objects.filter(
                workspace_changeset_id=cs_id
            ).exists():
                return
        except Exception:
            # If the DB check itself fails, err on the side of not cleaning
            # up (the DB may still be transiently unavailable).
            return
        if self._workspace is None:
            return
        try:
            if hasattr(self._workspace, "cleanup_orphan"):
                self._workspace.cleanup_orphan(cs_id)
        except Exception as exc:
            logger.warning("workspace cleanup_orphan(%s) failed: %s", cs_id, exc)

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

            # Item 2: full routing/provider check against the SQL-recorded provider.
            route_err = _check_provider_routing(
                self._router, changeset, cr.provider_name,
            )
            if route_err is not None:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": route_err.code,
                        "error_summary": route_err.message[:200],
                    },
                )
                return ChangeRequestResult(ok=False, error=route_err)

            # Item 9: duplicate targets.
            dup_err = _check_duplicate_targets(changeset)
            if dup_err is not None:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.VALIDATION_FAILED,
                    actor=user,
                    previous_state=current_state.value,
                    resulting_state=current_state.value,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": dup_err.code,
                        "error_summary": dup_err.message[:200],
                    },
                )
                return ChangeRequestResult(ok=False, error=dup_err)

            validation_issues: list[dict] = []
            for op in changeset.operations:
                if not op.collection:
                    validation_issues.append({"code": "missing_collection", "item_id": op.item_id})
                if not op.item_id:
                    validation_issues.append({"code": "missing_item_id", "collection": op.collection})

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
                        # Item 3: scope get_by_id to the operation's collection.
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

        # Item 9: mirror the workspace manifest state (advisory for
        # validation/rejection). On failure, record a bounded
        # ``workspace_sync_failed`` metadata entry and emit a dedicated
        # WORKSPACE_SYNC_FAILED audit event — NOT a duplicate
        # VALIDATION_SUCCEEDED entry.
        try:
            from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
            synced = _safe_workspace_transition(
                self._workspace, cr.workspace_changeset_id, _WsState.VALIDATED,
            )
            if not synced:
                try:
                    with transaction.atomic():
                        cr_sync = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                        meta = dict(cr_sync.metadata or {})
                        meta["workspace_sync_failed"] = {
                            "target_state": "validated",
                            "at_lifecycle_state": LifecycleState.VALIDATED.value,
                        }
                        cr_sync.metadata = meta
                        cr_sync.save(update_fields=["metadata", "updated_at"])
                except Exception:
                    pass
                try:
                    append_audit_event(
                        change_request=cr,
                        event_type=AuditEventType.WORKSPACE_SYNC_FAILED,
                        actor=user,
                        previous_state=LifecycleState.VALIDATED.value,
                        resulting_state=LifecycleState.VALIDATED.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail={"target_state": "validated"},
                    )
                except Exception:
                    # Item 9: audit failure must not crash the request.
                    logger.warning(
                        "workspace_sync_failed audit event failed for %s",
                        cr.request_id,
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

            # Item 1: workspace is MANDATORY for approval. No workspace →
            # workspace.unavailable, no state mutation.
            if self._workspace is None:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.APPROVAL_DENIED,
                    actor=user,
                    previous_state=cr.lifecycle_state,
                    resulting_state=cr.lifecycle_state,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": "workspace.unavailable",
                        "error_summary": "Workspace required for approval.",
                    },
                )
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "workspace.unavailable",
                        "Workspace is required to approve a change request.",
                    ),
                )
            # Item 1: integrity check on persisted proposal. Any integrity
            # failure is an ``approval.denied`` audit event; no state mutation.
            changeset, load_err = _load_workspace_changeset_with_integrity(
                self._workspace, cr.workspace_changeset_id, cr.payload_hash,
            )
            if load_err is not None:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.APPROVAL_DENIED,
                    actor=user,
                    previous_state=cr.lifecycle_state,
                    resulting_state=cr.lifecycle_state,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": load_err.code,
                        "error_summary": load_err.message[:200],
                    },
                )
                return ChangeRequestResult(ok=False, error=load_err)

            # Item 1: provider routing must still match at approval time.
            route_err = _check_provider_routing(
                self._router, changeset, cr.provider_name,
            )
            if route_err is not None:
                append_audit_event(
                    change_request=cr,
                    event_type=AuditEventType.APPROVAL_DENIED,
                    actor=user,
                    previous_state=cr.lifecycle_state,
                    resulting_state=cr.lifecycle_state,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": route_err.code,
                        "error_summary": route_err.message[:200],
                    },
                )
                return ChangeRequestResult(ok=False, error=route_err)

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

        # Item 9: mirror rejection to the workspace manifest (advisory). On
        # failure record ``workspace_sync_failed`` in metadata and emit a
        # dedicated WORKSPACE_SYNC_FAILED audit event.
        try:
            from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
            synced = _safe_workspace_transition(
                self._workspace, cr.workspace_changeset_id, _WsState.REJECTED,
            )
            if not synced:
                try:
                    with transaction.atomic():
                        cr_sync = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                        meta = dict(cr_sync.metadata or {})
                        meta["workspace_sync_failed"] = {
                            "target_state": "rejected",
                            "at_lifecycle_state": LifecycleState.REJECTED.value,
                        }
                        cr_sync.metadata = meta
                        cr_sync.save(update_fields=["metadata", "updated_at"])
                except Exception:
                    pass
                try:
                    append_audit_event(
                        change_request=cr,
                        event_type=AuditEventType.WORKSPACE_SYNC_FAILED,
                        actor=user,
                        previous_state=LifecycleState.REJECTED.value,
                        resulting_state=LifecycleState.REJECTED.value,
                        provider=cr.provider_name,
                        correlation_id=correlation_id,
                        detail={"target_state": "rejected"},
                    )
                except Exception:
                    logger.warning(
                        "workspace_sync_failed audit event failed for %s",
                        cr.request_id,
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

        provider_name = cr_check.provider_name

        # Item 2: authoritative routing check against SQL-recorded provider.
        route_err = _check_provider_routing(self._router, changeset, provider_name)
        if route_err is not None:
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
                        "error_code": route_err.code,
                        "error_summary": route_err.message[:200],
                    },
                )
            except Exception:
                pass
            return ChangeRequestResult(ok=False, error=route_err)

        # Item 9: duplicate targets — reject before any lock or mutation.
        dup_err = _check_duplicate_targets(changeset)
        if dup_err is not None:
            return ChangeRequestResult(ok=False, error=dup_err)

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

        # Item 17: wrap lock acquisition and map to structured busy error.
        try:
            request_lock_ctx = request_lock(request_id, locks_dir, timeout=timeout)
            request_lock_ctx.__enter__()
        except TimeoutError:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.busy",
                    "Another apply is in progress for this request; try again.",
                ),
            )
        except Exception as exc:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.busy",
                    f"Could not acquire request lock: {type(exc).__name__}",
                ),
            )
        try:
            try:
                provider_lock_ctx = provider_lock(provider_name, locks_dir, timeout=timeout)
                provider_lock_ctx.__enter__()
            except TimeoutError:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.busy",
                        "Another apply is in progress for this provider; try again.",
                    ),
                )
            except Exception as exc:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.busy",
                        f"Could not acquire provider lock: {type(exc).__name__}",
                    ),
                )
            try:
                return self._apply_locked(
                    request_id=request_id,
                    user=user,
                    expected_version=expected_version,
                    changeset=changeset,
                    provider_name=provider_name,
                    correlation_id=correlation_id,
                )
            finally:
                provider_lock_ctx.__exit__(None, None, None)
        finally:
            request_lock_ctx.__exit__(None, None, None)

    def _apply_locked(
        self,
        *,
        request_id: str,
        user: Any,
        expected_version: int,
        changeset: Any,
        provider_name: str,
        correlation_id: str,
    ) -> ChangeRequestResult:
        """Body of :meth:`apply_change_request`, executed under both locks."""
        cfg = self._config
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
        prep_digest = ""
        prep_entry_count = 0
        if adapter is not None and _adapter_fully_supports_rollback(adapter):
            try:
                prep_result = adapter.prepare(cr.workspace_changeset_id, changeset)
                # Support both typed and untyped adapter implementations.
                prep_digest = getattr(prep_result, "artifact_digest", "") or ""
                prep_entry_count = getattr(prep_result, "entry_count", 0) or 0
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

        # Item 8: persist artifact digest and entry count in SQL BEFORE mutation.
        if prep_digest:
            with transaction.atomic():
                cr = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                cr.rollback_artifact_digest = prep_digest
                meta = dict(cr.metadata or {})
                meta["rollback_artifact_entry_count"] = prep_entry_count
                cr.metadata = meta
                cr.save(update_fields=[
                    "rollback_artifact_digest", "metadata", "updated_at",
                ])

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
            # Item 16: try to reflect FAILED in the workspace; log if that fails.
            try:
                from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
                if not _safe_workspace_transition(
                    self._workspace, cr.workspace_changeset_id, _WsState.FAILED,
                ):
                    logger.warning(
                        "workspace FAILED sync unavailable for %s after apply exception",
                        cr.workspace_changeset_id,
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

        # Item 10: durable SQL marker BEFORE workspace result persistence,
        # so reconciliation can prove application completed even if the
        # workspace result write later fails.
        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            meta = dict(cr2.metadata or {})
            meta["application_completed"] = True
            meta["provider_name"] = cr2.provider_name
            meta["applied_count"] = len(apply_result.applied)
            meta["correlation_id"] = correlation_id
            cr2.metadata = meta
            cr2.save(update_fields=["metadata", "updated_at"])

        # Steps 5+6: Record post-application state and persist result artifact.
        # Any failure after a successful mutation → reconciliation_required.
        _recon_needed = False
        _recon_error = ""

        if adapter is not None and _adapter_fully_supports_rollback(adapter):
            try:
                # Item 2/8: bind post-state to the artifact digest we
                # recorded. Legacy adapters are already rejected upstream by
                # ``_adapter_fully_supports_rollback``; we do NOT fall back
                # to positional-only signatures.
                adapter.record_applied(
                    cr.workspace_changeset_id,
                    artifact_digest=prep_digest,
                )
            except Exception as exc:
                _recon_needed = True
                _recon_error = str(exc)[:500]

        _result_meta: dict = {
            "applied_count": len(apply_result.applied),
            "correlation_id": correlation_id,
        }
        if not _recon_needed:
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

        # Item 9: mirror apply success in the workspace manifest. Failure to
        # sync APPLIED post-mutation is escalated to reconciliation_required
        # (the SQL row still carries ``application_completed`` and
        # verification evidence, so reconciliation can safely finalize).
        applied_sync_ok = True
        try:
            from cauldron_workspace_flatfile.store import ChangeSetState as _WsState
            applied_sync_ok = _safe_workspace_transition(
                self._workspace, cr.workspace_changeset_id, _WsState.APPLIED,
            )
        except Exception:
            applied_sync_ok = False

        if not applied_sync_ok:
            with transaction.atomic():
                cr3 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                cr3.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
                cr3.request_version += 1
                cr3.last_error_code = "application.reconciliation_required"
                cr3.last_error_summary = "Workspace APPLIED sync failed after apply."
                cr3.save(update_fields=[
                    "lifecycle_state", "request_version",
                    "last_error_code", "last_error_summary", "updated_at",
                ])
                append_audit_event(
                    change_request=cr3,
                    event_type=AuditEventType.RECONCILIATION_FAILED,
                    actor=user,
                    previous_state=LifecycleState.APPLIED.value,
                    resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail={
                        "error_code": "application.reconciliation_required",
                        "error_summary": "workspace_applied_sync_failed",
                    },
                )
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "application.reconciliation_required",
                    "Workspace APPLIED sync failed; reconciliation required.",
                ),
                request_id=request_id,
                lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value,
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
        # Item 13: fail closed when the adapter does not fully implement the
        # rollback protocol — never treat a missing method as informational.
        if not _adapter_fully_supports_rollback(adapter):
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

        # Item 17: structured lock-timeout error.
        try:
            request_lock_ctx = request_lock(request_id, locks_dir, timeout=timeout)
            request_lock_ctx.__enter__()
        except TimeoutError:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.busy",
                    "Another rollback is in progress for this request; try again.",
                ),
            )
        except Exception as exc:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(
                    "operations.busy",
                    f"Could not acquire request lock: {type(exc).__name__}",
                ),
            )
        try:
            try:
                provider_lock_ctx = provider_lock(provider_name, locks_dir, timeout=timeout)
                provider_lock_ctx.__enter__()
            except TimeoutError:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.busy",
                        "Another rollback is in progress for this provider; try again.",
                    ),
                )
            except Exception as exc:
                return ChangeRequestResult(
                    ok=False,
                    error=OperationError(
                        "operations.busy",
                        f"Could not acquire provider lock: {type(exc).__name__}",
                    ),
                )
            try:
                return self._rollback_locked(
                    request_id=request_id,
                    user=user,
                    expected_version=expected_version,
                    provider_name=provider_name,
                    adapter=adapter,
                    force=force,
                    correlation_id=correlation_id,
                )
            finally:
                provider_lock_ctx.__exit__(None, None, None)
        finally:
            request_lock_ctx.__exit__(None, None, None)

    def _rollback_locked(
        self,
        *,
        request_id: str,
        user: Any,
        expected_version: int,
        provider_name: str,
        adapter: Any,
        force: bool,
        correlation_id: str,
    ) -> ChangeRequestResult:
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
        rollback_needs_recon = False
        try:
            # Item 2/8: pass the trusted artifact digest from SQL. We do not
            # fall back to legacy call shapes — adapters advertising
            # supports_rollback=True must implement the v2 signature.
            adapter.rollback(
                cr.workspace_changeset_id,
                force=force,
                is_superuser=bool(getattr(user, "is_superuser", False)),
                expected_artifact_digest=cr.rollback_artifact_digest or "",
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
            elif exc_type_name == "RollbackReconciliationRequired":
                rollback_needs_recon = True
                rollback_error_code = "rollback.reconciliation_required"
            elif exc_type_name in ("RollbackArtifactInvalid", "PathEscapeError"):
                rollback_error_code = "rollback.artifact_invalid"
            rollback_error = str(exc)[:500]

        # Item 5: on successful canonical rollback + provider marker, the
        # sequence is:
        #   1. adapter.rollback() -> internally calls record_rolled_back()
        #   2. adapter.verify_rolled_back_state() (evidence, may fail)
        #   3. SQL: metadata["rollback_completed"] = True + evidence
        #   4. workspace.save_rollback_result()
        #   5. SQL: finalize rolled_back lifecycle state
        # A failure at step 4 keeps rollback_completed in SQL and transitions
        # to reconciliation_required. Reconciliation can then finalize from
        # SQL evidence + verify_rolled_back_state alone.
        verification_failed = False
        verification_reason = ""
        verification_status = ""
        persistence_failed = False
        persistence_reason = ""
        entry_count_from_meta = int(
            (cr.metadata or {}).get("rollback_artifact_entry_count") or 0
        )
        if rollback_ok:
            # Item 3: verify with trusted SQL evidence.
            try:
                vr = adapter.verify_rolled_back_state(
                    cr.workspace_changeset_id,
                    expected_artifact_digest=cr.rollback_artifact_digest or "",
                    expected_entry_count=entry_count_from_meta,
                )
                verification_status = getattr(vr, "status", "") or ""
                if verification_status != "verified":
                    verification_failed = True
                    verification_reason = getattr(vr, "reason", "") or "verification failed"
            except Exception as exc:
                # Item 3: any exception from verify is fail-closed.
                verification_failed = True
                verification_reason = str(exc)[:200]
                verification_status = "error"

            # Item 5 step 3: durable SQL rollback_completed marker (before
            # workspace persistence) with bounded verification evidence.
            # Item 7: clear application_completed so reconciliation does not
            # see both markers simultaneously (which would be interpreted as
            # contradictory evidence).
            with transaction.atomic():
                cr_mark = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                meta = dict(cr_mark.metadata or {})
                meta["rollback_completed"] = True
                meta.pop("application_completed", None)
                meta["rollback_verification"] = {
                    "status": verification_status[:64],
                    "reason": verification_reason[:200],
                }
                cr_mark.metadata = meta
                cr_mark.save(update_fields=["metadata", "updated_at"])

            # Item 5 step 4: persist workspace rollback_result. Failure here
            # must not undo the SQL rollback_completed marker — reconciliation
            # can finalize from evidence alone.
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
                # Item 5 step 6: finalize rolled_back lifecycle state.
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
                # Item 5: workspace/verify failure post-mutation → reconciliation.
                # SQL rollback_completed remains set.
                if persistence_failed:
                    code = "application.reconciliation_required"
                    summary = persistence_reason
                else:
                    code = "rollback.verification_failed"
                    summary = verification_reason
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
            elif rollback_needs_recon:
                # Item 6: preflight passed but Phase 2 or provider marker
                # failed. State is uncertain — attempt one round of provider
                # verification, store bounded evidence in metadata, then
                # transition to reconciliation_required. Never rollback_failed.
                _rr_status = ""
                _rr_reason = ""
                try:
                    _rr_vr = adapter.verify_rolled_back_state(
                        cr.workspace_changeset_id,
                        expected_artifact_digest=cr.rollback_artifact_digest or "",
                        expected_entry_count=entry_count_from_meta,
                    )
                    _rr_status = getattr(_rr_vr, "status", "") or ""
                    _rr_reason = getattr(_rr_vr, "reason", "") or ""
                except Exception as _rr_exc:
                    _rr_status = "error"
                    _rr_reason = str(_rr_exc)[:200]
                _rr_meta = dict(cr2.metadata or {})
                _rr_meta["rollback_verification"] = {
                    "status": _rr_status[:64],
                    "reason": _rr_reason[:200],
                }
                cr2.metadata = _rr_meta
                cr2.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
                cr2.last_error_code = rollback_error_code
                cr2.last_error_summary = rollback_error
                cr2.request_version += 1
                cr2.save(update_fields=["lifecycle_state", "last_error_code", "last_error_summary", "request_version", "metadata", "updated_at"])
                append_audit_event(
                    change_request=cr2,
                    event_type=AuditEventType.RECONCILIATION_FAILED,
                    actor=user,
                    previous_state=LifecycleState.ROLLING_BACK.value,
                    resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                    provider=cr.provider_name,
                    correlation_id=correlation_id,
                    detail={"error_code": rollback_error_code, "error_summary": rollback_error},
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
            if persistence_failed:
                code = "application.reconciliation_required"
                summary = persistence_reason
            else:
                code = "rollback.verification_failed"
                summary = verification_reason
            return ChangeRequestResult(
                ok=False,
                error=OperationError(code, summary or code),
                request_id=request_id,
                lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value,
            )
        if rollback_needs_recon:
            return ChangeRequestResult(
                ok=False,
                error=OperationError(rollback_error_code, rollback_error),
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
        """Inspect and optionally finalize interrupted change requests.

        Item 8: mutating reconciliation acquires both a per-request and a
        per-provider file lock. Timeouts map to ``operations.busy`` without
        leaking lock paths. Dry-run uses the same decision engine but takes
        no locks and notes that evidence may be transient.
        """
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

            # Item 8: dry-run uses the same decision engine, acquires no
            # locks, and notes evidence may be transient.
            if dry_run:
                self._reconcile_decide(cr, entry, mutate=False, user=user)
                entry.setdefault("dry_run_evidence_notice",
                                 "Evidence read without locks; may be transient.")
                results.append(entry)
                continue

            # Item 8: mutating mode requires locks_dir.
            if locks_dir is None:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = "operations.busy: locks unavailable"
                results.append(entry)
                continue

            try:
                with request_lock(cr.request_id, locks_dir, timeout=timeout):
                    try:
                        with provider_lock(cr.provider_name, locks_dir, timeout=timeout):
                            # Item 8: re-read row with select_for_update
                            # before deciding, to see the freshest evidence.
                            try:
                                with transaction.atomic():
                                    fresh = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
                            except ContentChangeRequest.DoesNotExist:
                                entry["action"] = "leave_ambiguous"
                                entry["reason"] = "Row missing at lock time."
                                results.append(entry)
                                continue
                            self._reconcile_decide(fresh, entry, mutate=True, user=user)
                    except TimeoutError:
                        entry["action"] = "leave_ambiguous"
                        entry["reason"] = "operations.busy: provider lock timeout"
            except TimeoutError:
                entry["action"] = "leave_ambiguous"
                entry["reason"] = "operations.busy: request lock timeout"
            results.append(entry)

        return results

    # -------------------------------------------------------------------------
    # Reconciliation helpers
    # -------------------------------------------------------------------------

    def _reconcile_decide(
        self,
        cr: ContentChangeRequest,
        entry: dict,
        *,
        mutate: bool,
        user: Any,
    ) -> None:
        """Single decision engine used by both dry-run and mutating reconciliation.

        Items 10, 11, 12: uses provider verification bound to SQL evidence,
        rejects malformed/typed-wrong artifacts, and refuses to finalize
        when the underlying workspace proposal has been tampered with.
        """
        adapter = get_adapter(cr.provider_name) if cr.provider_name else None
        state = cr.lifecycle_state

        # Item 11: absent adapter or unsupported verification is never finalizable.
        if not _adapter_fully_supports_rollback(adapter):
            entry["action"] = "requires_manual_review"
            entry["reason"] = "Rollback adapter unavailable or incomplete."
            if mutate and state != LifecycleState.RECONCILIATION_REQUIRED.value:
                self._mark_reconciliation_required(cr, user)
            return

        # Item 12: verify payload integrity before trusting proposal-related evidence.
        # We only treat *tampering* codes as fatal here — a missing/unreadable
        # payload doesn't disprove that mutation completed, so we still allow
        # provider evidence to disambiguate. Tampering codes force manual review
        # unless provider evidence independently proves a completed rollback.
        payload_ok = True
        payload_err_code = ""
        payload_err_msg = ""
        if self._workspace is not None:
            _, load_err = _load_workspace_changeset_with_integrity(
                self._workspace, cr.workspace_changeset_id, cr.payload_hash,
            )
            if load_err is not None and load_err.code in (
                "workspace.payload_integrity_mismatch",
                "workspace.force_not_allowed",
            ):
                payload_ok = False
                payload_err_code = load_err.code
                payload_err_msg = load_err.message

        # Typed result validation (Item 11).
        app_result = (
            self._workspace.load_application_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )
        rb_result = (
            self._workspace.load_rollback_result(cr.workspace_changeset_id)
            if self._workspace is not None else None
        )
        typed_applied = bool(app_result and isinstance(app_result, dict) and app_result.get("result_type") == "applied")
        typed_rolled_back = bool(rb_result and isinstance(rb_result, dict) and rb_result.get("result_type") == "rolled_back")

        # Item 10: SQL durable markers.
        meta = dict(cr.metadata or {})
        sql_applied_marker = bool(meta.get("application_completed"))
        sql_rollback_marker = bool(meta.get("rollback_completed"))

        # Item 3: verification must use trusted SQL evidence.
        expected_digest = cr.rollback_artifact_digest or ""
        expected_count = int(meta.get("rollback_artifact_entry_count") or 0)
        try:
            vapp = adapter.verify_applied_state(
                cr.workspace_changeset_id,
                expected_artifact_digest=expected_digest,
                expected_entry_count=expected_count,
            )
        except Exception as exc:
            vapp = None
            v_apply_err = str(exc)[:120]
        else:
            v_apply_err = ""
        try:
            vrb = adapter.verify_rolled_back_state(
                cr.workspace_changeset_id,
                expected_artifact_digest=expected_digest,
                expected_entry_count=expected_count,
            )
        except Exception as exc:
            vrb = None
            v_rb_err = str(exc)[:120]
        else:
            v_rb_err = ""
        vapp_verified = vapp is not None and getattr(vapp, "status", "") == "verified"
        vrb_verified = vrb is not None and getattr(vrb, "status", "") == "verified"

        # Item 7: contradiction detection — both applied AND rolled-back
        # evidence present makes any finalization unsafe.
        contradictory_evidence = (
            (vapp_verified and vrb_verified)
            or (sql_applied_marker and sql_rollback_marker)
            or (typed_applied and typed_rolled_back)
        )
        if contradictory_evidence:
            if mutate:
                _record_reconciliation_reason(
                    cr,
                    "reconciliation.contradictory_evidence",
                    "Both applied and rolled-back evidence present.",
                )
                self._mark_reconciliation_required(
                    cr, user,
                    extra_reason={
                        "code": "reconciliation.contradictory_evidence",
                        "summary": "Both applied and rolled-back evidence present.",
                    },
                )
            entry["action"] = "requires_manual_review"
            entry["reason"] = "Contradictory apply/rollback evidence."
            return

        # For rolling_back, never rewrite to applied even if provider says so.
        if state == LifecycleState.ROLLING_BACK.value:
            if not payload_ok and not vrb_verified:
                # Item 12: force-tampering blocks finalization unless provider
                # evidence independently proves a completed rollback.
                if mutate:
                    _record_reconciliation_reason(cr, payload_err_code, payload_err_msg)
                    self._mark_reconciliation_required(cr, user, extra_reason={
                        "code": payload_err_code, "summary": payload_err_msg,
                    })
                entry["action"] = "leave_ambiguous"
                entry["reason"] = f"payload_integrity: {payload_err_code}"
                return
            if (typed_rolled_back or sql_rollback_marker) and vrb_verified:
                if mutate:
                    self._finalize_rolled_back(cr, user, rb_result or {})
                    entry["applied"] = True
                    entry["action"] = "finalize_rolled_back"
                else:
                    entry["action"] = "would_finalize_rolled_back"
                    entry["reason"] = "Verified rollback state via provider."
                return
            entry["action"] = "leave_ambiguous" if not mutate else "leave_ambiguous"
            entry["reason"] = "No confirmed rollback result / verification."
            if mutate:
                self._mark_reconciliation_required(cr, user)
            return

        if state == LifecycleState.APPLYING.value:
            # Item 7: tampered payload can NEVER finalize as applied. The only
            # bypass in this decision engine is a completed rollback with
            # independent provider evidence.
            if not payload_ok:
                if mutate:
                    _record_reconciliation_reason(cr, payload_err_code, payload_err_msg)
                    self._mark_reconciliation_required(cr, user, extra_reason={
                        "code": payload_err_code, "summary": payload_err_msg,
                    })
                entry["action"] = "leave_ambiguous"
                entry["reason"] = f"payload_integrity: {payload_err_code}"
                return
            # Item 10: SQL marker + provider verification finalizes even
            # without a workspace result file.
            if (typed_applied or sql_applied_marker) and vapp_verified:
                if mutate:
                    self._finalize_applied(cr, user, app_result or {"applied_count": meta.get("applied_count", 0)})
                    entry["applied"] = True
                    entry["action"] = "finalize_applied"
                else:
                    entry["action"] = "would_finalize_applied"
                    entry["reason"] = "SQL marker or workspace result confirms application; provider verified."
                return
            entry["action"] = "leave_ambiguous"
            if not vapp_verified:
                entry["reason"] = f"Provider verification not verified: {getattr(vapp, 'reason', '') or v_apply_err}"[:120]
            else:
                entry["reason"] = "No confirmed application result found."
            if mutate:
                self._mark_reconciliation_required(cr, user)
            return

        if state == LifecycleState.RECONCILIATION_REQUIRED.value:
            # Item 7: a tampered payload NEVER finalizes as applied. The only
            # exception is a completed rollback proven by independent provider
            # evidence (verify_rolled_back_state returns "verified" and we
            # have a rollback marker or typed rollback result).
            if not payload_ok:
                can_finalize_rolled_back = (
                    (typed_rolled_back or sql_rollback_marker) and vrb_verified
                )
                if not can_finalize_rolled_back:
                    if mutate:
                        _record_reconciliation_reason(cr, payload_err_code, payload_err_msg)
                    entry["action"] = "requires_manual_review"
                    entry["reason"] = f"payload_integrity: {payload_err_code}"
                    return
                # Fall through to the rolled_back finalize branch below.
                if mutate:
                    self._finalize_rolled_back(cr, user, rb_result or {})
                    entry["applied"] = True
                    entry["action"] = "finalize_rolled_back"
                else:
                    entry["action"] = "would_finalize_rolled_back"
                    entry["reason"] = "Verified rolled-back state via provider."
                return
            if (typed_applied or sql_applied_marker) and vapp_verified:
                if mutate:
                    self._finalize_applied(cr, user, app_result or {"applied_count": meta.get("applied_count", 0)})
                    entry["applied"] = True
                    entry["action"] = "finalize_applied"
                else:
                    entry["action"] = "would_finalize_applied"
                    entry["reason"] = "Verified applied state via provider."
                return
            if (typed_rolled_back or sql_rollback_marker) and vrb_verified:
                if mutate:
                    self._finalize_rolled_back(cr, user, rb_result or {})
                    entry["applied"] = True
                    entry["action"] = "finalize_rolled_back"
                else:
                    entry["action"] = "would_finalize_rolled_back"
                    entry["reason"] = "Verified rolled-back state via provider."
                return
            entry["action"] = "requires_manual_review"
            entry["reason"] = "State remains ambiguous — needs manual review."
            return

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
                "applied_count": app_result.get("applied_count") if isinstance(app_result, dict) else None,
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
        extra_reason: dict | None = None,
    ) -> None:
        with transaction.atomic():
            cr2 = ContentChangeRequest.objects.select_for_update().get(pk=cr.pk)
            if cr2.lifecycle_state == LifecycleState.RECONCILIATION_REQUIRED.value:
                if extra_reason:
                    meta = dict(cr2.metadata or {})
                    meta["reconciliation_failure_reason"] = extra_reason
                    cr2.metadata = meta
                    cr2.save(update_fields=["metadata", "updated_at"])
                return
            previous = cr2.lifecycle_state
            cr2.lifecycle_state = LifecycleState.RECONCILIATION_REQUIRED.value
            cr2.request_version += 1
            if extra_reason:
                meta = dict(cr2.metadata or {})
                meta["reconciliation_failure_reason"] = extra_reason
                cr2.metadata = meta
                cr2.save(update_fields=["lifecycle_state", "request_version", "metadata", "updated_at"])
            else:
                cr2.save(update_fields=["lifecycle_state", "request_version", "updated_at"])
            append_audit_event(
                change_request=cr2,
                event_type=AuditEventType.RECONCILIATION_FAILED,
                actor=user,
                previous_state=previous,
                resulting_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                provider=cr.provider_name,
            )
