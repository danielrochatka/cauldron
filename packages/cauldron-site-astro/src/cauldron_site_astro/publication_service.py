"""SiteChangeSet publication service — shared preview/publish workflow.

This module contains the *authoritative* orchestration for creating a
:class:`SiteChangeSet`, building its scoped preview, and publishing it to the
live site. Both the AI tool handlers (``site_tools.py``) and the human admin
views (in cauldron-admin-content) call these methods rather than duplicating
the logic.

Design constraints
------------------
- Accepts plain domain values (User, list[str], etc.) — no HTTP request, no
  admin-AI tool context, no admin-AI tool result.
- Returns structured dataclasses so callers can format their own responses.
- prepare() runs an integrity gate (via ``_check_request_integrity``) BEFORE
  creating any SiteChangeSet row. Missing, terminal, or operation-free requests
  fail closed with no side effects.
- publish() applies content requests individually (no outer transaction.atomic).
  On partial failure it compensates previously applied requests via
  ``ContentOperationService.compensate_for_publication_failure`` and tracks
  restoration of Astro output (``restore_output``) and theme CSS
  (``set_active_css``) independently. ``compensated=True`` in
  ``publish_build_result`` ONLY when canonical content, Astro output, and theme
  (where applicable) were all verified/restored. Partial restoration sets
  ``requires_reconciliation=True`` and retains the output backup for operator
  recovery.
- ``RECONCILIATION_REQUIRED`` from ``apply_change_request`` means the canonical
  provider WAS mutated; ``requires_reconciliation`` is always set and FS state
  is preserved as-is so reconciliation operators see consistent on-disk state.
- Must not import cauldron-ai-admin. Both callers get a common service.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from cauldron_site_astro.models import SiteChangeSet
from cauldron_site_astro.service import get_build_service


logger = logging.getLogger(__name__)

# Bounded lengths so unbounded backends can't leak into result payloads.
_MAX_EXC_MSG = 200
_MAX_BUILD_LOG_TAIL = 500

# Permission required to publish a SiteChangeSet. Same permission the AI
# handler uses for site.publish.
_PERM_PUBLISH = "cauldron_content_operations.apply_content_changes"


# ---------------------------------------------------------------------------
# Lifecycle allowlist (Correction 4)
# ---------------------------------------------------------------------------
#
# The publish() integrity check uses an explicit allowlist of lifecycle states
# rather than relying on "not terminal" heuristics. Any state not present here
# — including unknown or future values — is refused. This prevents partial
# publishes from being kicked off against a request in an ambiguous or
# transitional state (applying/rolling_back/reconciliation_required).
#
# States that can transition into apply *without* approval:
#   * proposed   — validate + apply chain will move it forward
#   * validated  — apply directly; no re-validation needed
#   * apply_failed — retry: apply_change_request supports APPLY_FAILED → APPLYING
#
# When require_approval is True the ONLY eligible state is:
#   * approved
#
# All other states — applying, rolling_back, rolled_back, applied,
# reconciliation_required, rejected, rollback_failed, or any unknown value —
# fail closed with an explicit diagnostic. See ``lifecycle.py`` for the
# canonical state definitions; we import LifecycleState rather than
# duplicating the string literals.


def _publishable_states(require_approval: bool) -> "frozenset[str]":
    """Return the allowlisted lifecycle states for the publish path."""
    from cauldron_content_operations.lifecycle import LifecycleState
    if require_approval:
        return frozenset({LifecycleState.APPROVED.value})
    return frozenset({
        LifecycleState.PROPOSED.value,
        LifecycleState.VALIDATED.value,
        LifecycleState.APPLY_FAILED.value,
    })


def _get_require_approval() -> bool:
    """Load ``require_approval`` from the content-operations config.

    Extracted to a module-level helper so tests can patch it directly. Raises
    on any config error — callers MUST catch and fail closed so a broken
    config never silently disables the approval gate.
    """
    from cauldron_content_operations.config import get_operations_config
    cfg = get_operations_config()
    return bool(getattr(cfg, "require_approval", False))


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PrepareResult:
    """Result of :meth:`SiteChangeSetService.prepare`."""

    ok: bool
    change_set_id: str = ""
    status: str = ""
    pages_built: int = 0
    preview_url: str = ""
    message: str = ""
    build_log_tail: str = ""


@dataclass
class InspectResult:
    """Result of :meth:`SiteChangeSetService.inspect`."""

    ok: bool
    change_set_id: str = ""
    status: str = ""
    pages_built: int = 0
    preview_url: str = ""
    created_at: str = ""
    message: str = ""
    publish_build_result: dict = field(default_factory=dict)
    content_request_ids: list = field(default_factory=list)
    # True when the caller may reasonably retry publishing the same change
    # set. False when compensation succeeded (change requests are now
    # terminally rolled back) or compensation failed (reconciliation
    # required). Derived from ``publish_build_result`` on inspect().
    retryable: bool = True


@dataclass
class PublishResult:
    """Result of :meth:`SiteChangeSetService.publish`."""

    ok: bool
    change_set_id: str = ""
    status: str = ""
    pages_built: int = 0
    live_url: str = ""
    applied_request_ids: list = field(default_factory=list)
    message: str = ""
    build_log_tail: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_exc(exc: BaseException) -> str:
    """Bound exception text so backends can't leak into results."""
    return str(exc)[:_MAX_EXC_MSG]


def _coerce_run_id(raw) -> "_uuid.UUID | None":
    """Convert a run_id string into a UUID, or ``None`` for malformed input."""
    if not raw:
        return None
    try:
        return _uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _get_content_operation_service():
    """Return a ContentOperationService instance, or ``None`` if unavailable.

    Imports from ``cauldron_content_operations.service_factory`` — the module
    that owns the ContentOperationService wiring. The previous import path
    ran through the optional ``cauldron_admin_content`` package which made
    Site Astro runtime-depend on it; ``cauldron.content.operations`` is a
    hard ``requires`` of ``cauldron.site.astro`` so this import always
    resolves.
    """
    try:
        from cauldron_content_operations.service_factory import get_service
    except ImportError:
        return None
    try:
        return get_service()
    except Exception:
        return None


def _extract_draft_items(
    content_request_ids: list[str],
) -> "tuple[list[str], list, list[str]]":
    """Return (item_ids, extra_items, deleted_item_ids) for the given change requests.

    ``item_ids`` — all affected item ids (includes deletes, for tracking).
    ``extra_items`` — synthetic draft items for create/update ops to inject
    into the controlled build as if they are already applied.
    ``deleted_item_ids`` — item ids for delete operations; callers pass these
    as ``excluded_item_ids`` to ``build_preview`` so the published baseline
    version of a deleted page is omitted from the controlled build.

    Mirrors the extraction logic used by the original site_tools handlers so
    the preview and publish builds see the same draft content the operator
    reviewed. See site_tools._extract_draft_items for the full contract.
    """
    if not content_request_ids:
        return [], [], []

    try:
        from cauldron_content_operations.models import ContentChangeRequest
    except Exception:
        return [], [], []

    service = _get_content_operation_service()
    workspace = getattr(service, "_workspace", None) if service is not None else None

    item_ids: list[str] = []
    extra_items: list = []
    deleted_item_ids: list[str] = []
    seen_item_ids: set[str] = set()
    seen_inject_ids: set[str] = set()
    seen_deleted_ids: set[str] = set()

    for req_id in content_request_ids:
        try:
            cr = ContentChangeRequest.objects.get(request_id=req_id)
        except Exception:
            continue

        ops_to_process = []

        ws_id = getattr(cr, "workspace_changeset_id", "")
        if workspace is not None and ws_id:
            try:
                changeset = workspace.load_changeset(ws_id)
                ops_to_process = list(getattr(changeset, "operations", ()) or ())
            except Exception:
                pass

        if not ops_to_process:
            raw_ops = getattr(cr, "operations", None)
            if isinstance(raw_ops, list):
                from types import SimpleNamespace
                ops_to_process = [
                    SimpleNamespace(**op) if isinstance(op, dict) else op
                    for op in raw_ops
                ]

        for op in ops_to_process:
            op_item_id = str(getattr(op, "item_id", "") or "")
            op_kind = str(getattr(op, "kind", "") or "")
            op_data = getattr(op, "data", None) or {}
            op_body = getattr(op, "body", "") or ""
            op_slug = getattr(op, "slug", "") or ""
            op_schema = getattr(op, "schema", "") or "page"
            op_collection = getattr(op, "collection", "") or ""

            if op_item_id and op_item_id not in seen_item_ids:
                seen_item_ids.add(op_item_id)
                item_ids.append(op_item_id)

            if op_kind == "delete":
                if op_item_id and op_item_id not in seen_deleted_ids:
                    seen_deleted_ids.add(op_item_id)
                    deleted_item_ids.append(op_item_id)
                continue

            # Read op.status (top-level field set by build_page_operation()).
            # ContentStatus is a str Enum so its members compare equal to their
            # string values; both enum members and raw strings are accepted.
            # Fail closed for missing or unknown status — do not default to published.
            _raw_status = getattr(op, "status", None)
            if _raw_status == "published":
                op_final_status = "published"
            elif _raw_status == "draft":
                op_final_status = "draft"
            else:
                # Missing or unrecognised status — skip this op.
                continue

            # Draft-status create/update ops (e.g. unpublish) are excluded from
            # the preview build just like deletes — the page must not appear in
            # the scoped preview.
            if op_final_status == "draft":
                if op_item_id and op_item_id not in seen_deleted_ids:
                    seen_deleted_ids.add(op_item_id)
                    deleted_item_ids.append(op_item_id)
                continue

            if not op_slug:
                continue

            inject_id = op_item_id or op_slug
            if inject_id in seen_inject_ids:
                continue
            seen_inject_ids.add(inject_id)

            try:
                from types import SimpleNamespace
                from cauldron_content.contracts import ContentStatus
                extra_items.append(SimpleNamespace(
                    id=inject_id,
                    collection=op_collection,
                    slug=op_slug,
                    status=ContentStatus.PUBLISHED,
                    schema=op_schema,
                    data=op_data if isinstance(op_data, dict) else {},
                    body=op_body,
                    hash="",
                    provider="",
                    source_ref="",
                ))
            except Exception:
                pass

    return item_ids, extra_items, deleted_item_ids


# ---------------------------------------------------------------------------
# Integrity check (Correction 2)
# ---------------------------------------------------------------------------


def _check_request_integrity(
    content_request_ids: list[str],
    *,
    require_approval: bool,
) -> "tuple[bool, str]":
    """Verify every requested change request is eligible for preview/publish.

    Runs BEFORE any SiteChangeSet or preview build so a broken/missing request
    cannot silently disappear from the preview. Reuses ContentOperationService's
    read contracts — this function does NOT re-implement the validation engine;
    it only asserts that the requested requests exist, are in an allowlisted
    lifecycle state, and — when approval is required — have been approved.

    Returns ``(ok, message)``. On success ``message`` is empty. On failure
    ``message`` contains a bounded operator-facing diagnostic and no
    SiteChangeSet should be created.

    Fails CLOSED when the ContentChangeRequest model cannot be imported: the
    module has ``cauldron.content.operations`` in its declared dependencies,
    so the model must be available in every deployment. Silently skipping
    integrity checks was the pre-correction behaviour that let requested
    changes disappear from previews.
    """
    if not content_request_ids:
        # Callers must supply a non-empty list; this is a safety net.
        return False, "content_request_ids must be a non-empty list of strings."

    try:
        from cauldron_content_operations.models import ContentChangeRequest
    except ImportError as exc:
        return False, (
            f"cauldron.content.operations is required for publish integrity checks: "
            f"{_safe_exc(exc)}"
        )

    allowed = _publishable_states(require_approval)

    for req_id in content_request_ids:
        try:
            cr = ContentChangeRequest.objects.get(request_id=req_id)
        except ContentChangeRequest.DoesNotExist:
            return False, f"Content request {req_id!r} not found."
        # Any other DB/config failure means the environment is broken; do NOT
        # silently continue — that reintroduces the pre-correction bug.
        state = cr.lifecycle_state
        if state not in allowed:
            if require_approval:
                return False, (
                    f"Content request {req_id!r} must be approved before "
                    f"publishing (current state: {state!r})."
                )
            return False, (
                f"Content request {req_id!r} is in state {state!r}; only "
                f"{sorted(allowed)!r} are eligible for publish."
            )

    # Cross-check: the persisted operations must load. Skipping missing
    # workspace payloads was another silent-failure mode.
    service = _get_content_operation_service()
    workspace = getattr(service, "_workspace", None) if service is not None else None
    for req_id in content_request_ids:
        try:
            cr = ContentChangeRequest.objects.get(request_id=req_id)
        except ContentChangeRequest.DoesNotExist:
            # Already handled above; belt-and-braces.
            return False, f"Content request {req_id!r} not found."
        ws_id = getattr(cr, "workspace_changeset_id", "") or ""
        raw_ops = getattr(cr, "operations", None)
        has_raw_ops = isinstance(raw_ops, list) and len(raw_ops) > 0
        if workspace is not None and ws_id:
            try:
                changeset = workspace.load_changeset(ws_id)
            except Exception as exc:
                # If we do not have inline operations to fall back to, fail
                # closed — otherwise the preview would silently omit this
                # request.
                if not has_raw_ops:
                    return False, (
                        f"Could not load workspace changeset for {req_id!r}: "
                        f"{_safe_exc(exc)}"
                    )
                changeset = None
            if changeset is not None and not list(
                getattr(changeset, "operations", ()) or ()
            ) and not has_raw_ops:
                return False, (
                    f"Content request {req_id!r} has no operations to publish."
                )
        elif not has_raw_ops:
            # No workspace and no inline operations: nothing to publish.
            return False, (
                f"Content request {req_id!r} has no operations to publish."
            )

    return True, ""


# ---------------------------------------------------------------------------
# Publish-time CR loader (Correction 4 — extracted for testability)
# ---------------------------------------------------------------------------


def _fetch_eligible_change_requests(
    content_request_ids: list[str],
    allowed_states: "frozenset[str]",
    require_approval: bool,
) -> "tuple[dict, dict | None]":
    """Load ContentChangeRequests and verify their lifecycle states.

    Returns ``(loaded_crs, error_context)`` where *error_context* is ``None``
    on success. On failure *loaded_crs* is empty and *error_context* is a dict
    with ``"publish_build_result"`` (ready for ``cs.publish_build_result``) and
    ``"message"`` (ready for ``PublishResult.message``).

    Extracted from ``publish()`` so tests can patch this single function
    without needing real ContentChangeRequest rows for publish-flow tests that
    are exercising other concerns (CSS handoff, signal suppression, etc.).
    """
    from cauldron_content_operations.models import ContentChangeRequest

    loaded: dict = {}
    for req_id in content_request_ids:
        try:
            cr = ContentChangeRequest.objects.get(request_id=req_id)
        except ContentChangeRequest.DoesNotExist:
            return {}, {
                "publish_build_result": {
                    "error": f"content request {req_id!r} not found",
                    "applied": [],
                    "failed_request_id": req_id,
                },
                "message": f"Content request {req_id!r} not found.",
            }
        state = cr.lifecycle_state
        if state not in allowed_states:
            if require_approval:
                msg = (
                    f"Content request {req_id!r} must be approved before "
                    f"publishing (current state: {state!r})."
                )
            else:
                msg = (
                    f"Content request {req_id!r} is in state {state!r}; "
                    f"only {sorted(allowed_states)!r} are eligible for publish."
                )
            return {}, {
                "publish_build_result": {
                    "error": msg,
                    "applied": [],
                    "failed_request_id": req_id,
                },
                "message": msg,
            }
        loaded[req_id] = cr
    return loaded, None


# ---------------------------------------------------------------------------
# Signal suppression flag (set by publish() around the content-apply step)
# ---------------------------------------------------------------------------

# Imported lazily from apps.py to avoid a circular import at module load.
def _suppress_flag():
    from cauldron_site_astro.apps import _suppress_rebuild
    return _suppress_rebuild


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SiteChangeSetService:
    """Domain service for preview/publish of a :class:`SiteChangeSet`.

    Instances are cheap; construct one per request. The service resolves its
    dependencies (build service, content operation service) lazily so that
    tests can patch them.
    """

    # -- prepare ------------------------------------------------------------

    def prepare(
        self,
        actor: Any,
        content_request_ids: list[str],
        *,
        originating_run_id: str | None = None,
        description: str = "",
        staged_theme_css: str = "",
        style_request_id: str = "",
    ) -> PrepareResult:
        """Create a :class:`SiteChangeSet` and build a scoped preview.

        On success the change set transitions to ``DRAFT_READY`` and the
        preview URL is returned. On build failure it transitions to
        ``PREVIEW_FAILED``; the change set is still returned so the caller
        can surface the error state to the operator.

        Correction 2: integrity of every requested ContentChangeRequest is
        verified BEFORE any SiteChangeSet row is created. A missing,
        terminal, or otherwise ineligible request causes prepare() to fail
        without side effects — no SiteChangeSet, no build_preview call.

        ``style_request_id`` — when non-empty, the SiteChangeSet represents a
        pages-scope style proposal.  An empty ``content_request_ids`` is
        permitted in this case (style-only changeset).  After successful
        ``publish()``, the ``site_changeset_published`` signal carries this ID
        so the style request can be marked applied.
        """
        # Style-only changesets (style_request_id set, no content requests) are
        # allowed.  Content-only changesets still require at least one ID.
        is_style_only = bool(style_request_id) and not content_request_ids
        if not is_style_only:
            if not isinstance(content_request_ids, list) or not content_request_ids:
                return PrepareResult(
                    ok=False,
                    message="content_request_ids must be a non-empty list of strings.",
                )
        content_request_ids = [str(x) for x in (content_request_ids or [])]

        try:
            svc = get_build_service()
            cfg = svc._config
        except Exception as exc:
            return PrepareResult(
                ok=False,
                message=f"Could not load site build config: {_safe_exc(exc)}",
            )

        if not cfg.previews_root:
            return PrepareResult(
                ok=False,
                message="previews_root is not configured; cannot build preview.",
            )

        if content_request_ids:
            # Correction 2 / Item 5: fail closed if operations config is unavailable.
            try:
                require_approval = _get_require_approval()
            except Exception as exc:
                return PrepareResult(
                    ok=False,
                    message=(
                        f"Content operations configuration unavailable: {_safe_exc(exc)}"
                    ),
                )

            integrity_ok, integrity_msg = _check_request_integrity(
                content_request_ids, require_approval=require_approval,
            )
            if not integrity_ok:
                return PrepareResult(ok=False, message=integrity_msg)

        # Determine effective theme CSS.  Priority (highest to lowest):
        #   1. Explicitly passed staged_theme_css.
        #   2. Composed pages CSS from the registered PagesStyleProvider
        #      (cauldron-django-admin's UIOverrideStore).  This ensures that
        #      content-only publishes carry forward existing pages overrides
        #      and that style-only changesets carry the proposed overlay.
        #   3. Any staged.css already written under theme_root.
        theme_css = staged_theme_css or ""
        if not theme_css:
            try:
                from cauldron_content.pages_style import get_pages_style_provider
                _provider = get_pages_style_provider()
                if _provider is not None:
                    theme_css = _provider.get_composed_css() or ""
            except Exception:
                pass
        if not theme_css and cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_css = SiteThemeService(cfg.theme_root).get_staged_css() or ""
            except Exception:
                pass

        affected_item_ids, draft_extra_items, draft_deleted_ids = _extract_draft_items(content_request_ids)
        cs = SiteChangeSet.objects.create(
            status=SiteChangeSet.PREPARING,
            content_request_ids=content_request_ids,
            staged_theme_css=theme_css or "",
            originating_run_id=_coerce_run_id(originating_run_id),
            creator=actor if getattr(actor, "pk", None) is not None else None,
            affected_item_ids=affected_item_ids,
            style_request_id=style_request_id or "",
        )

        output_dir = Path(cfg.previews_root) / str(cs.id)
        try:
            result = svc.build_preview(
                output_dir=output_dir,
                item_ids_to_include=affected_item_ids or None,
                extra_items=draft_extra_items or None,
                excluded_item_ids=draft_deleted_ids or None,
                theme_css=theme_css or "",
            )
        except Exception as exc:
            cs.status = SiteChangeSet.PREVIEW_FAILED
            cs.save(update_fields=["status", "updated_at"])
            return PrepareResult(
                ok=False,
                change_set_id=str(cs.id),
                status=cs.status,
                message=f"Preview build raised an exception: {_safe_exc(exc)}",
            )

        if not result.ok:
            cs.status = SiteChangeSet.PREVIEW_FAILED
            cs.preview_dir = str(cs.id)
            cs.save(update_fields=["status", "preview_dir", "updated_at"])
            return PrepareResult(
                ok=False,
                change_set_id=str(cs.id),
                status=cs.status,
                message=f"Preview build failed: {(result.error or '')[:_MAX_EXC_MSG]}",
                build_log_tail=(result.build_log or "")[-_MAX_BUILD_LOG_TAIL:],
            )

        cs.status = SiteChangeSet.DRAFT_READY
        cs.preview_dir = str(cs.id)
        cs.draft_ready_at = timezone.now()
        cs.save(update_fields=[
            "status", "preview_dir", "draft_ready_at", "updated_at",
        ])

        return PrepareResult(
            ok=True,
            change_set_id=str(cs.id),
            status=cs.status,
            pages_built=result.pages_built,
            preview_url=cs.get_preview_url(),
            message=(
                f"Change set {cs.id} is draft_ready ({result.pages_built} page(s))."
            ),
        )

    # -- find_reusable_change_set -------------------------------------------

    def find_reusable_change_set(self, request_id: str) -> str | None:
        """Return the ID of an existing reusable SiteChangeSet for *request_id*.

        Returns the most recent DRAFT_READY or PUBLISH_FAILED change set that
        includes *request_id* in its content_request_ids. Uses Python-level
        filtering (bounded scan of recent candidates) to avoid the JSONField
        __contains limitation on SQLite.
        Returns None on any failure or if no suitable change set exists.
        """
        try:
            candidates = (
                SiteChangeSet.objects
                .filter(status__in=[SiteChangeSet.DRAFT_READY, SiteChangeSet.PUBLISH_FAILED])
                .order_by("-created_at")[:50]
            )
            for cs in candidates:
                if request_id in (cs.content_request_ids or []):
                    return str(cs.id)
            return None
        except Exception:
            return None

    # -- inspect ------------------------------------------------------------

    def inspect(self, change_set_id: str) -> InspectResult:
        """Return the current status, page count and preview URL for *change_set_id*."""
        try:
            cs = SiteChangeSet.objects.get(id=change_set_id)
        except (SiteChangeSet.DoesNotExist, ValueError):
            return InspectResult(
                ok=False,
                change_set_id=str(change_set_id),
                message=f"Change set {change_set_id!r} not found.",
            )

        pages_built = 0
        try:
            svc = get_build_service()
            cfg = svc._config
        except Exception:
            cfg = None

        if cfg is not None and cfg.previews_root and cs.preview_dir:
            preview_dir = Path(cfg.previews_root) / cs.preview_dir
            if preview_dir.exists() and preview_dir.is_dir():
                pages_built = sum(1 for _ in preview_dir.rglob("*.html"))

        publish_build_result = cs.publish_build_result or {}

        # Correction 3: derive retryability from publish_build_result. A change
        # set is not retryable when compensation succeeded (change requests are
        # now terminally rolled_back), nor when reconciliation is required.
        retryable = True
        if cs.status == SiteChangeSet.PUBLISH_FAILED:
            if publish_build_result.get("requires_reconciliation"):
                retryable = False
            elif publish_build_result.get("compensated"):
                # Successful compensation moved every applied CR to rolled_back,
                # which is terminal. The operator must create new change
                # requests; the same SiteChangeSet cannot be retried.
                retryable = False

        return InspectResult(
            ok=True,
            change_set_id=str(cs.id),
            status=cs.status,
            pages_built=pages_built,
            preview_url=cs.get_preview_url(),
            created_at=cs.created_at.isoformat() if cs.created_at else "",
            publish_build_result=publish_build_result,
            content_request_ids=list(cs.content_request_ids or []),
            retryable=retryable,
        )

    # -- publish ------------------------------------------------------------

    def publish(self, actor: Any, change_set_id: str) -> PublishResult:
        """Publish a draft-ready :class:`SiteChangeSet` to the live site."""
        # Permission gate — same permission the AI handler required.
        if not getattr(actor, "has_perm", lambda _p: False)(_PERM_PUBLISH):
            return PublishResult(
                ok=False,
                change_set_id=str(change_set_id),
                message=(
                    f"Actor lacks {_PERM_PUBLISH!r}; cannot publish change set."
                ),
            )

        # Lock the row to prevent concurrent PUBLISHING transitions (#7).
        # The transaction commits (releasing the lock) before the slow
        # build + FS steps so we do not hold a DB lock for the entire publish.
        with transaction.atomic():
            try:
                cs = SiteChangeSet.objects.select_for_update().get(id=change_set_id)
            except (SiteChangeSet.DoesNotExist, ValueError):
                return PublishResult(
                    ok=False,
                    change_set_id=str(change_set_id),
                    message=f"Change set {change_set_id!r} not found.",
                )

            # Idempotent: re-publishing an already-published change set is a no-op.
            if cs.status == SiteChangeSet.PUBLISHED:
                return PublishResult(
                    ok=True,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    pages_built=(cs.publish_build_result or {}).get("pages_built", 0),
                    live_url="/",
                    applied_request_ids=list(
                        (cs.publish_build_result or {}).get("applied", []) or []
                    ),
                    message=f"Change set {cs.id} was already published.",
                )

            # Allow retry from publish_failed: all FS state is restored on each failure.
            if cs.status not in (SiteChangeSet.DRAFT_READY, SiteChangeSet.PUBLISH_FAILED):
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=(
                        f"Change set is in status {cs.status!r}; "
                        f"only draft_ready or publish_failed change sets can be published."
                    ),
                )

            cs.status = SiteChangeSet.PUBLISHING
            cs.save(update_fields=["status", "updated_at"])
        # Lock released; cs.status == PUBLISHING is now committed.

        try:
            svc = get_build_service()
            cfg = svc._config
        except Exception as exc:
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = {"error": f"build config unavailable: {_safe_exc(exc)}", "applied": []}
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return PublishResult(
                ok=False,
                change_set_id=str(cs.id),
                message=f"Could not load site build config: {_safe_exc(exc)}",
            )

        content_service = _get_content_operation_service()

        # ---- Step A: Prepare integrity check (Correction 4) --------------
        # Use the explicit lifecycle allowlist rather than "not terminal".
        # Fail CLOSED when the ContentChangeRequest model cannot be loaded —
        # cauldron.site.astro already requires cauldron.content.operations, so
        # a missing model means the deployment is broken and we must not
        # silently proceed.
        try:
            from cauldron_content_operations.models import ContentChangeRequest
        except ImportError as exc:
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = {
                "error": f"content-operations models unavailable: {_safe_exc(exc)}",
                "applied": [],
            }
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return PublishResult(
                ok=False,
                change_set_id=str(cs.id),
                status=cs.status,
                message=(
                    "cauldron.content.operations is required for publish; "
                    "cannot load ContentChangeRequest."
                ),
            )

        # Item 5: fail closed when operations config cannot be loaded — the
        # SiteChangeSet is already PUBLISHING, so update it to PUBLISH_FAILED
        # before returning.
        try:
            require_approval = _get_require_approval()
        except Exception as exc:
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = {
                "error": f"content operations configuration unavailable: {_safe_exc(exc)}",
                "applied": [],
            }
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return PublishResult(
                ok=False,
                change_set_id=str(cs.id),
                status=cs.status,
                message=(
                    f"Content operations configuration unavailable: {_safe_exc(exc)}"
                ),
            )

        allowed_states = _publishable_states(require_approval)

        # Map req_id → current ContentChangeRequest for lifecycle/version checks.
        _loaded_crs, _cr_error = _fetch_eligible_change_requests(
            list(cs.content_request_ids or []), allowed_states, require_approval,
        )
        if _cr_error is not None:
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = _cr_error["publish_build_result"]
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return PublishResult(
                ok=False,
                change_set_id=str(cs.id),
                status=cs.status,
                message=_cr_error["message"],
            )

        # ---- Step 1: Validate content requests ---------------------------
        # Use the current request_version for optimistic locking. Skip validate
        # for VALIDATED/APPROVED states — re-validating would be rejected by
        # the state machine.
        from cauldron_content_operations.lifecycle import LifecycleState as _LS
        validated: dict = {}

        for req_id in cs.content_request_ids or ():
            if content_service is None:
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": "content-operations service unavailable",
                    "applied": [],
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message="Content operations service is unavailable; cannot apply changes.",
                )

            cr = _loaded_crs.get(req_id)
            current_state = cr.lifecycle_state if cr is not None else ""
            current_version = cr.request_version if cr is not None else 0

            if current_state in (_LS.VALIDATED.value, _LS.APPROVED.value):
                # Already validated; skip to avoid a lifecycle state machine error.
                from types import SimpleNamespace as _NS
                validated[req_id] = _NS(ok=True, request_version=current_version)
                continue

            try:
                v = content_service.validate_change_request(
                    req_id, user=actor, expected_version=current_version,
                )
            except Exception as exc:
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": f"validate raised: {_safe_exc(exc)}",
                    "applied": [],
                    "failed_request_id": req_id,
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=f"Validation of {req_id!r} raised: {_safe_exc(exc)}",
                )

            if not getattr(v, "ok", False):
                err = getattr(v, "error", None)
                err_msg = getattr(err, "message", "validation failed")
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": f"validate failed for {req_id}: {err_msg[:_MAX_EXC_MSG]}",
                    "applied": [],
                    "failed_request_id": req_id,
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=f"Validation of {req_id!r} failed: {err_msg[:_MAX_EXC_MSG]}",
                )

            validated[req_id] = v

        # ---- Step 2: Build ------------------------------------------------
        _, publish_extra_items, publish_deleted_ids = _extract_draft_items(cs.content_request_ids or [])

        tmp_build_dir = tempfile.mkdtemp(prefix="cauldron_pub_")
        output_snapshot: "Path | None" = None
        # Item 4: when output restoration fails during compensation we must
        # NOT discard the backup in the finally block — operators need it
        # to reconstruct the previous state manually.
        _keep_output_snapshot = False

        try:
            try:
                result = svc.build_preview(
                    output_dir=tmp_build_dir,
                    item_ids_to_include=cs.affected_item_ids or None,
                    extra_items=publish_extra_items or None,
                    excluded_item_ids=publish_deleted_ids or None,
                    theme_css=cs.staged_theme_css or "",
                )
            except Exception as exc:
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": f"build raised: {_safe_exc(exc)}",
                    "applied": [],
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=f"Publish build raised an exception: {_safe_exc(exc)}",
                )

            if not result.ok:
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": (result.error or "")[:_MAX_EXC_MSG],
                    "applied": [],
                    "build_log_tail": (result.build_log or "")[-_MAX_BUILD_LOG_TAIL:],
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=f"Publish build failed: {(result.error or '')[:_MAX_EXC_MSG]}",
                    build_log_tail=(result.build_log or "")[-_MAX_BUILD_LOG_TAIL:],
                )

            # ---- Step 3: Snapshot current active CSS ---------------------
            theme_svc = None
            prev_active_css = ""
            if cs.staged_theme_css and cfg.theme_root:
                try:
                    from cauldron_site_astro.theme import SiteThemeService
                    theme_svc = SiteThemeService(cfg.theme_root)
                    prev_active_css = theme_svc.get_active_css()
                except Exception:
                    pass

            # ---- Step 4: Promote output with rollback snapshot -----------
            try:
                output_snapshot = svc.promote_output_with_backup(tmp_build_dir)
            except Exception as exc:
                cs.status = SiteChangeSet.PUBLISH_FAILED
                cs.publish_build_result = {
                    "error": f"output promotion failed: {_safe_exc(exc)}",
                    "applied": [],
                }
                cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                return PublishResult(
                    ok=False,
                    change_set_id=str(cs.id),
                    status=cs.status,
                    message=f"Output promotion failed: {_safe_exc(exc)}",
                )

            # ---- Step 5: Promote CSS -------------------------------------
            if theme_svc is not None:
                try:
                    theme_svc.stage_css(cs.staged_theme_css)
                    theme_svc.promote_staged()
                except Exception as exc:
                    # Apply the same restoration discipline as Step 6: track
                    # output and CSS restoration independently, retain the
                    # output backup when restore_output fails, and always
                    # attempt to restore prev_active_css because promote_staged
                    # may have moved staged.css over active.css before raising.
                    _theme_promo_err = _safe_exc(exc)
                    _step5_output_restored = False
                    _step5_output_restore_err = ""
                    _step5_css_restored = False
                    _step5_css_restore_err = ""

                    # Restore Astro output — track success/failure independently.
                    if output_snapshot is not None:
                        try:
                            svc.restore_output(output_snapshot)
                            output_snapshot = None
                            _step5_output_restored = True
                        except Exception as restore_exc:
                            _step5_output_restore_err = _safe_exc(restore_exc)
                            # Retain backup for manual recovery — the outer
                            # finally MUST NOT discard it.
                            _keep_output_snapshot = True
                    else:
                        # Nothing to restore (no backup taken).
                        _step5_output_restored = True

                    # active.css may have been changed by promote_staged()
                    # before the exception. stage_css() only writes staged.css
                    # so it cannot change active.css; promote_staged() moves
                    # staged→active via rename(), which is effectively atomic
                    # on POSIX but may leave active.css changed. Always attempt
                    # restore.
                    try:
                        theme_svc.set_active_css(prev_active_css)
                        _step5_css_restored = True
                    except Exception as css_exc:
                        _step5_css_restore_err = _safe_exc(css_exc)

                    _step5_requires_reconciliation = (
                        not _step5_output_restored or not _step5_css_restored
                    )

                    result_meta: dict = {
                        "error": f"theme promotion failed: {_theme_promo_err}",
                        "applied": [],
                        "requires_reconciliation": _step5_requires_reconciliation,
                    }
                    if not _step5_output_restored:
                        result_meta["output_restored"] = False
                        result_meta["output_restore_error"] = _step5_output_restore_err
                    if not _step5_css_restored:
                        result_meta["css_restored"] = False
                        result_meta["css_restore_error"] = _step5_css_restore_err

                    cs.status = SiteChangeSet.PUBLISH_FAILED
                    cs.publish_build_result = result_meta
                    cs.save(update_fields=["status", "publish_build_result", "updated_at"])

                    if _step5_requires_reconciliation:
                        step5_msg = (
                            f"Theme promotion failed: {_theme_promo_err}; "
                            f"output/CSS recovery partial — manual reconciliation required."
                        )
                    else:
                        step5_msg = f"Theme promotion failed: {_theme_promo_err}"

                    return PublishResult(
                        ok=False,
                        change_set_id=str(cs.id),
                        status=cs.status,
                        message=step5_msg,
                    )

            # ---- Step 6: Apply content requests (Corrections 2 / 3 / 4) --
            # Each apply_change_request call is internally atomic. We track
            # every apply so that if a later request fails we can compensate
            # via ContentOperationService.compensate_for_publication_failure —
            # a narrow internal path that uses the same ReversibleMutationAdapter
            # rollback as rollback_change_request but does NOT require the
            # publisher to hold rollback_content_changes (which is unrelated to
            # apply_content_changes).
            #
            # Signal-suppression flag is set here so canonical_content_changed
            # fired by apply_change_request does NOT trigger a redundant rebuild.
            applied_ids: list[str] = []
            applied_versions: dict[str, int] = {}
            _apply_err: str = ""
            _failed_request_id: str = ""
            _failed_apply_result: Any = None
            _failed_lifecycle: str = ""
            # Item 3: RECONCILIATION_REQUIRED from apply_change_request means
            # the canonical provider WAS mutated but post-application workspace
            # persistence failed. We cannot auto-compensate the failed request
            # via rollback and must not claim a clean canonical state.
            _failed_was_mutated: bool = False

            suppress = _suppress_flag()
            suppress.active = True
            try:
                for req_id in cs.content_request_ids or ():
                    v = validated[req_id]
                    try:
                        a = content_service.apply_change_request(
                            req_id,
                            user=actor,
                            expected_version=getattr(v, "request_version", 0),
                        )
                    except Exception as exc:
                        _apply_err = f"apply raised for {req_id!r}: {_safe_exc(exc)}"
                        _failed_request_id = req_id
                        break
                    if not getattr(a, "ok", False):
                        err = getattr(a, "error", None)
                        err_msg = getattr(err, "message", "apply failed")[:_MAX_EXC_MSG]
                        _apply_err = f"apply failed for {req_id!r}: {err_msg}"
                        _failed_request_id = req_id
                        _failed_apply_result = a
                        _failed_lifecycle = getattr(a, "lifecycle_state", "") or ""
                        if _failed_lifecycle == _LS.RECONCILIATION_REQUIRED.value:
                            _failed_was_mutated = True
                        break
                    applied_ids.append(req_id)
                    applied_versions[req_id] = getattr(a, "request_version", 0)

                if _apply_err:
                    # Item 2: only run the compensation loop when at least one
                    # request had been applied. Otherwise there is nothing to
                    # compensate and we must NOT set compensated=True vacuously.
                    compensations: list[dict] = []
                    if applied_ids:
                        all_verified = True
                        for prev_req_id in reversed(applied_ids):
                            try:
                                comp = content_service.compensate_for_publication_failure(
                                    prev_req_id,
                                    user=actor,
                                    expected_version=applied_versions.get(prev_req_id, 0),
                                )
                            except Exception as exc:
                                # Method absent on older service versions or
                                # unexpected runtime error — treat as un-verified.
                                comp = None
                                comp_err = _safe_exc(exc)
                                all_verified = False
                                compensations.append({
                                    "request_id": prev_req_id,
                                    "ok": False,
                                    "verified": False,
                                    "error_code": "compensation.exception",
                                    "error_message": comp_err,
                                })
                                continue
                            entry = {
                                "request_id": prev_req_id,
                                "ok": bool(getattr(comp, "ok", False)),
                                "verified": bool(getattr(comp, "verified", False)),
                                "lifecycle_state": getattr(comp, "lifecycle_state", "") or "",
                                "error_code": getattr(comp, "error_code", "") or "",
                                "error_message": (
                                    getattr(comp, "error_message", "") or ""
                                )[:_MAX_EXC_MSG],
                            }
                            compensations.append(entry)
                            if not entry["verified"]:
                                all_verified = False
                    else:
                        # Nothing was applied before failure — nothing to
                        # compensate. compensated must NOT be True.
                        all_verified = True  # vacuous (no prior applies)

                    # Item 3: the failed request itself has uncertain canonical
                    # state when the provider was mutated (RECONCILIATION_REQUIRED).
                    # Even if prior requests were compensated, this cannot count
                    # as clean restoration.
                    requires_reconciliation = (
                        (not all_verified) or _failed_was_mutated
                    )

                    # Item 4: track output and theme restoration independently.
                    # Restoration is safe when the canonical state is confirmed
                    # clean — either because nothing was applied AND the failed
                    # request was not mutated (APPLY_FAILED or an exception
                    # before mutation), or because all prior applies were
                    # verified-compensated AND the failed request was not
                    # mutated.
                    _output_restored = False
                    _theme_restored = False
                    _output_restore_err = ""
                    _theme_restore_err = ""

                    _canonical_clean = (
                        (not _failed_was_mutated)
                        and (not applied_ids or all_verified)
                    )

                    if _canonical_clean:
                        # Canonical content is confirmed restored. Now restore
                        # the FS components. Each is tracked independently so
                        # a partial failure is honestly reported.
                        if output_snapshot is not None:
                            try:
                                svc.restore_output(output_snapshot)
                                output_snapshot = None
                                _output_restored = True
                            except Exception as exc:
                                _output_restore_err = _safe_exc(exc)
                                requires_reconciliation = True
                                # Retain backup — do NOT discard in finally.
                                _keep_output_snapshot = True
                        else:
                            # Nothing to restore (no backup taken).
                            _output_restored = True

                        if theme_svc is not None:
                            try:
                                theme_svc.set_active_css(prev_active_css)
                                _theme_restored = True
                            except Exception as exc:
                                _theme_restore_err = _safe_exc(exc)
                                requires_reconciliation = True
                        else:
                            # N/A — no theme service in play.
                            _theme_restored = True
                    else:
                        # Canonical not fully restored or failed request was
                        # mutated. Preserve on-disk state so reconciliation
                        # operators see the state that matches applied CRs.
                        _output_restored = False
                        _theme_restored = False

                    # Item 4: compensated=True ONLY when every applicable
                    # component was restored AND at least one request had
                    # been applied to compensate.
                    compensated = bool(
                        applied_ids
                        and all_verified
                        and _output_restored
                        and _theme_restored
                        and not _failed_was_mutated
                    )

                    # Structured restoration info for operators.
                    restoration_info: dict = {}
                    if applied_ids:
                        restoration_info["canonical_compensated"] = all_verified
                    if output_snapshot is not None or _output_restore_err:
                        restoration_info["output_restored"] = _output_restored
                        if _output_restore_err:
                            restoration_info["output_restore_error"] = _output_restore_err
                    if theme_svc is not None:
                        restoration_info["theme_restored"] = _theme_restored
                        if _theme_restore_err:
                            restoration_info["theme_restore_error"] = _theme_restore_err
                    if _failed_was_mutated:
                        restoration_info["failed_request_lifecycle"] = _failed_lifecycle

                    cs.status = SiteChangeSet.PUBLISH_FAILED
                    cs.publish_build_result = {
                        "error": _apply_err,
                        "applied": [],
                        "failed_request_id": _failed_request_id,
                        "compensated": compensated,
                        "requires_reconciliation": requires_reconciliation,
                        "compensations": compensations,
                        **restoration_info,
                    }
                    cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                    if requires_reconciliation:
                        message = (
                            f"{_apply_err}; compensation could not verify "
                            f"restoration — manual reconciliation required."
                        )
                    elif compensated:
                        message = (
                            f"{_apply_err}; all applied content requests were "
                            f"compensated and canonical state restored."
                        )
                    else:
                        # Nothing was applied — no compensation needed; the
                        # failed request can potentially be retried.
                        message = _apply_err
                    return PublishResult(
                        ok=False,
                        change_set_id=str(cs.id),
                        status=cs.status,
                        message=message,
                    )
            finally:
                suppress.active = False

            # ---- Step 7: Commit ------------------------------------------
            svc.discard_output_backup(output_snapshot)
            output_snapshot = None

            cs.status = SiteChangeSet.PUBLISHED
            cs.published_at = timezone.now()
            cs.publish_build_result = {
                "applied": applied_ids,
                "pages_built": result.pages_built,
            }
            cs.save(update_fields=[
                "status", "published_at", "publish_build_result", "updated_at",
            ])

            # Notify subscribers (e.g. style-request lifecycle handler).
            try:
                from cauldron_site_astro.signals import site_changeset_published
                site_changeset_published.send(
                    sender=SiteChangeSetService,
                    changeset_id=str(cs.id),
                    staged_theme_css=cs.staged_theme_css or "",
                    style_request_id=cs.style_request_id or "",
                )
            except Exception:
                logger.exception(
                    "cauldron.site.astro: site_changeset_published signal failed "
                    "for changeset %s; publication succeeded but handlers errored.",
                    cs.id,
                )

            return PublishResult(
                ok=True,
                change_set_id=str(cs.id),
                status=cs.status,
                pages_built=result.pages_built,
                live_url="/",
                applied_request_ids=applied_ids,
                message=(
                    f"Change set {cs.id} published successfully "
                    f"({result.pages_built} page(s))."
                ),
            )

        finally:
            shutil.rmtree(tmp_build_dir, ignore_errors=True)
            # Item 4: honor _keep_output_snapshot so a failed restore_output
            # does not destroy the operator's only remaining copy of prior
            # output state.
            if output_snapshot is not None and not _keep_output_snapshot:
                try:
                    svc.discard_output_backup(output_snapshot)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def get_publication_service() -> SiteChangeSetService:
    """Return a fresh :class:`SiteChangeSetService` instance."""
    return SiteChangeSetService()
