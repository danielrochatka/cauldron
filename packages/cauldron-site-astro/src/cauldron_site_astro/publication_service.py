"""SiteChangeSet publication service — shared preview/publish workflow.

This module contains the *authoritative* orchestration for creating a
:class:`SiteChangeSet`, building its scoped preview, and publishing it to the
live site. Both the AI tool handlers (``site_tools.py``) and the human admin
views (in cauldron-admin-content) call these methods rather than duplicating
the logic.

Design constraints
------------------
- Accepts plain domain values (User, list[str], etc.) — no HTTP request, no
  admin-AI tool context, no admin-AI tool result. Callers translate results
  into their own response shapes.
- Returns structured dataclasses so callers can format their own messages.
- The publish() method reproduces the 7-step atomic transaction from the
  original ``_handle_publish`` in ``site_tools.py`` verbatim: validate →
  build → snapshot output → promote output → promote CSS → apply content
  (transaction.atomic) → discard snapshot. On failure at ANY step the FS
  and DB are restored to their pre-publish state.
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
    """Return a ContentOperationService instance, or ``None`` if unavailable."""
    try:
        from cauldron_admin_content.service_factory import get_service
    except ImportError:
        return None
    try:
        return get_service()
    except Exception:
        return None


def _extract_draft_items(
    content_request_ids: list[str],
) -> "tuple[list[str], list]":
    """Return (item_ids, extra_items) for the given change requests.

    Mirrors the extraction logic used by the original site_tools handlers so
    the preview and publish builds see the same draft content the operator
    reviewed. See site_tools._extract_draft_items for the full contract.
    """
    if not content_request_ids:
        return [], []

    try:
        from cauldron_content_operations.models import ContentChangeRequest
    except Exception:
        return [], []

    service = _get_content_operation_service()
    workspace = getattr(service, "_workspace", None) if service is not None else None

    item_ids: list[str] = []
    extra_items: list = []
    seen_item_ids: set[str] = set()
    seen_inject_ids: set[str] = set()

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
                    status=ContentStatus.DRAFT,
                    schema=op_schema,
                    data=op_data if isinstance(op_data, dict) else {},
                    body=op_body,
                    hash="",
                    provider="",
                    source_ref="",
                ))
            except Exception:
                pass

    return item_ids, extra_items


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
    ) -> PrepareResult:
        """Create a :class:`SiteChangeSet` and build a scoped preview.

        On success the change set transitions to ``DRAFT_READY`` and the
        preview URL is returned. On build failure it transitions to
        ``PREVIEW_FAILED``; the change set is still returned so the caller
        can surface the error state to the operator.
        """
        if not isinstance(content_request_ids, list) or not content_request_ids:
            return PrepareResult(
                ok=False,
                message="content_request_ids must be a non-empty list of strings.",
            )
        content_request_ids = [str(x) for x in content_request_ids]

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

        # Auto-load staged theme CSS if not explicitly supplied.
        theme_css = staged_theme_css or ""
        if not theme_css and cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_css = SiteThemeService(cfg.theme_root).get_staged_css() or ""
            except Exception:
                pass

        affected_item_ids, draft_extra_items = _extract_draft_items(content_request_ids)
        cs = SiteChangeSet.objects.create(
            status=SiteChangeSet.PREPARING,
            content_request_ids=content_request_ids,
            staged_theme_css=theme_css or "",
            originating_run_id=_coerce_run_id(originating_run_id),
            creator=actor if getattr(actor, "pk", None) is not None else None,
            affected_item_ids=affected_item_ids,
        )

        output_dir = Path(cfg.previews_root) / str(cs.id)
        try:
            result = svc.build_preview(
                output_dir=output_dir,
                item_ids_to_include=affected_item_ids or None,
                extra_items=draft_extra_items or None,
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

        return InspectResult(
            ok=True,
            change_set_id=str(cs.id),
            status=cs.status,
            pages_built=pages_built,
            preview_url=cs.get_preview_url(),
            created_at=cs.created_at.isoformat() if cs.created_at else "",
            publish_build_result=cs.publish_build_result or {},
            content_request_ids=list(cs.content_request_ids or []),
        )

    # -- publish ------------------------------------------------------------

    def publish(self, actor: Any, change_set_id: str) -> PublishResult:
        """Publish a draft-ready :class:`SiteChangeSet` to the live site.

        Reproduces the 7-step atomic transaction from the original
        ``_handle_publish`` in ``site_tools.py`` verbatim.
        """
        # Permission gate — same permission the AI handler required.
        if not getattr(actor, "has_perm", lambda _p: False)(_PERM_PUBLISH):
            return PublishResult(
                ok=False,
                change_set_id=str(change_set_id),
                message=(
                    f"Actor lacks {_PERM_PUBLISH!r}; cannot publish change set."
                ),
            )

        try:
            cs = SiteChangeSet.objects.get(id=change_set_id)
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

        try:
            svc = get_build_service()
            cfg = svc._config
        except Exception as exc:
            return PublishResult(
                ok=False,
                change_set_id=str(cs.id),
                message=f"Could not load site build config: {_safe_exc(exc)}",
            )

        cs.status = SiteChangeSet.PUBLISHING
        cs.save(update_fields=["status", "updated_at"])

        # ---- Step 1: Validate all content requests ------------------------
        content_service = _get_content_operation_service()
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

            try:
                v = content_service.validate_change_request(req_id, user=actor)
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
        _, publish_extra_items = _extract_draft_items(cs.content_request_ids or [])

        tmp_build_dir = tempfile.mkdtemp(prefix="cauldron_pub_")
        output_snapshot: "Path | None" = None

        try:
            try:
                result = svc.build_preview(
                    output_dir=tmp_build_dir,
                    item_ids_to_include=cs.affected_item_ids or None,
                    extra_items=publish_extra_items or None,
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
                    try:
                        svc.restore_output(output_snapshot)
                        output_snapshot = None
                    except Exception:
                        pass
                    cs.status = SiteChangeSet.PUBLISH_FAILED
                    cs.publish_build_result = {
                        "error": f"theme promotion failed: {_safe_exc(exc)}",
                        "applied": [],
                    }
                    cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                    return PublishResult(
                        ok=False,
                        change_set_id=str(cs.id),
                        status=cs.status,
                        message=f"Theme promotion failed: {_safe_exc(exc)}",
                    )

            # ---- Step 6: Apply content requests inside a single transaction
            # Signal-suppression flag is set here so canonical_content_changed
            # signal fired by apply_change_request does NOT trigger a redundant
            # rebuild — this publish already manages its own build.
            applied_ids: list[str] = []
            _apply_err: str = ""

            suppress = _suppress_flag()
            suppress.active = True
            try:
                try:
                    with transaction.atomic():
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
                                raise
                            if not getattr(a, "ok", False):
                                err = getattr(a, "error", None)
                                err_msg = getattr(err, "message", "apply failed")[:_MAX_EXC_MSG]
                                _apply_err = f"apply failed for {req_id!r}: {err_msg}"
                                raise RuntimeError(_apply_err)
                            applied_ids.append(req_id)
                except Exception:
                    try:
                        svc.restore_output(output_snapshot)
                        output_snapshot = None
                    except Exception:
                        pass
                    if theme_svc is not None:
                        try:
                            theme_svc.set_active_css(prev_active_css)
                        except Exception:
                            pass
                    cs.status = SiteChangeSet.PUBLISH_FAILED
                    cs.publish_build_result = {
                        "error": _apply_err or "DB apply transaction failed",
                        "applied": [],
                    }
                    cs.save(update_fields=["status", "publish_build_result", "updated_at"])
                    return PublishResult(
                        ok=False,
                        change_set_id=str(cs.id),
                        status=cs.status,
                        message=_apply_err or "Content apply transaction failed; all changes rolled back.",
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
            if output_snapshot is not None:
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
