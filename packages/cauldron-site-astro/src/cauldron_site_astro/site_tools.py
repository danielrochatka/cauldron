"""Admin AI site tools for cauldron-site-astro.

Seven tools drive the AI authoring workflow. Every result exposes only
Django URL paths and business-safe data — filesystem paths (frontend_root,
output_root, theme_root, previews_root) are never leaked to the model:

1. ``site.verify_root``        — run structured diagnostics on the live site (READ_ONLY)
2. ``site.inspect``            — read current build status (READ_ONLY)
3. ``site.stage_theme``        — stage a CSS theme for the next publish (PROPOSE)
4. ``site.propose_homepage``   — propose a homepage content change (PROPOSE)
5. ``site.prepare_change_set`` — create a SiteChangeSet + scoped preview (PROPOSE)
6. ``site.inspect_preview``    — read a change set's preview status (READ_ONLY)
7. ``site.publish``            — apply a draft-ready change set to live (MAINTENANCE)

Tools 5-7 operate on a persisted :class:`SiteChangeSet` (keyed by
``change_set_id``) so status transitions are durable across the multi-step
prepare -> review -> publish flow. Previews are scoped to just the content
requests attached to the change set, so unrelated in-flight drafts never
leak into an unrelated preview.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from cauldron_site_astro.service import get_build_service
from cauldron_site_astro.site_diagnostics import run_site_diagnostics

if TYPE_CHECKING:
    from cauldron_ai_admin.tools import AdminAIToolRegistry


# Bounded lengths for safety: never dump raw exception text or large logs.
_MAX_EXC_MSG = 200
_MAX_BUILD_LOG_TAIL = 500


def register(registry: "AdminAIToolRegistry") -> None:
    """Register all site tools into *registry*."""
    try:
        from cauldron_ai_admin.tools import AdminAIToolDefinition, RiskLevel
    except ImportError:
        return

    _OWNING_MODULE = "cauldron.site.astro"
    _PERM_VIEW = "cauldron_content_operations.view_published_content"
    _PERM_PROPOSE = "cauldron_content_operations.propose_content_changes"
    _PERM_MAINTAIN = "cauldron_content_operations.apply_content_changes"

    # ------------------------------------------------------------------
    # 1. site.verify_root
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.verify_root",
            version="1.0",
            description=(
                "Run structured diagnostics on the live site: checks whether "
                "the homepage content is published, whether the root index.html "
                "artifact exists and is non-empty, and whether a GET request to "
                "'/' returns 200 text/html. Returns healthy (boolean) and a "
                "per-check breakdown. Read-only."
            ),
            argument_schema={"type": "object", "properties": {}, "required": []},
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_VIEW,
            owning_module=_OWNING_MODULE,
        ),
        _handle_verify_root,
    )

    # ------------------------------------------------------------------
    # 2. site.inspect
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.inspect",
            version="1.0",
            description=(
                "Inspect the current public site build status: whether a live "
                "build exists and whether a staged theme CSS is pending. Read-only."
            ),
            argument_schema={"type": "object", "properties": {}, "required": []},
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_VIEW,
            owning_module=_OWNING_MODULE,
        ),
        _handle_site_inspect,
    )

    # ------------------------------------------------------------------
    # 3. site.stage_theme
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.stage_theme",
            version="1.0",
            description=(
                "Stage a CSS stylesheet as the proposed public-site theme. "
                "The CSS is NOT applied to the live site until site.publish "
                "is called. Does not trigger a build."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "css_content": {
                        "type": "string",
                        "description": "Full CSS content for the public-site theme.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of the proposed theme change.",
                    },
                },
                "required": ["css_content"],
            },
            risk_level=RiskLevel.PROPOSE,
            required_permission=_PERM_PROPOSE,
            owning_module=_OWNING_MODULE,
        ),
        _handle_stage_theme,
    )

    # ------------------------------------------------------------------
    # 4. site.propose_homepage
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.propose_homepage",
            version="1.0",
            description=(
                "Propose a homepage content change. Determines whether to "
                "create or update the homepage singleton based on whether it "
                "already exists, then creates a content change request. "
                "Returns cs_id for use with site.prepare_change_set."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Page title displayed in the browser tab and heading.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Page body content (markdown or HTML).",
                    },
                    "navigation_title": {
                        "type": "string",
                        "description": "Short label used in navigation menus.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Summary for listings and link previews.",
                    },
                    "seo_title": {
                        "type": "string",
                        "description": "SEO <title> tag override; defaults to title if omitted.",
                    },
                    "meta_description": {
                        "type": "string",
                        "description": "Meta description for search engines.",
                    },
                },
                "required": ["title", "body"],
            },
            risk_level=RiskLevel.PROPOSE,
            required_permission=_PERM_PROPOSE,
            owning_module=_OWNING_MODULE,
        ),
        _handle_propose_homepage,
    )

    # ------------------------------------------------------------------
    # 5. site.prepare_change_set
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.prepare_change_set",
            version="2.0",
            description=(
                "Create a SiteChangeSet from one or more content change "
                "requests plus an optional staged theme CSS, and build a "
                "scoped preview. The preview shows the published site with "
                "only the drafts from these change requests layered on top; "
                "unrelated in-flight drafts are excluded. Does not touch the "
                "live site. Returns change_set_id and a Django preview URL."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "content_request_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Content change-request IDs to include in this change set."
                        ),
                    },
                    "theme_css": {
                        "type": "string",
                        "description": (
                            "Optional CSS to preview alongside the drafts. "
                            "This CSS is only promoted to live on successful publish."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for this change set.",
                    },
                },
                "required": ["content_request_ids"],
            },
            risk_level=RiskLevel.PROPOSE,
            required_permission=_PERM_PROPOSE,
            owning_module=_OWNING_MODULE,
            timeout_seconds=180.0,
        ),
        _handle_prepare_change_set,
    )

    # ------------------------------------------------------------------
    # 6. site.inspect_preview
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.inspect_preview",
            version="2.0",
            description=(
                "Inspect a previously prepared SiteChangeSet preview. "
                "Returns status, page count, and the Django preview URL."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "change_set_id": {
                        "type": "string",
                        "description": "UUID returned by site.prepare_change_set.",
                    },
                },
                "required": ["change_set_id"],
            },
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_VIEW,
            owning_module=_OWNING_MODULE,
        ),
        _handle_inspect_preview,
    )

    # ------------------------------------------------------------------
    # 7. site.publish
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.publish",
            version="2.0",
            description=(
                "Publish a draft-ready SiteChangeSet to the live site: apply "
                "its content change requests, build the site with the staged "
                "theme, and promote both together. Requires confirm=true. If "
                "the build fails, no staged CSS is promoted and the live site "
                "is left untouched."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "change_set_id": {
                        "type": "string",
                        "description": "UUID of the SiteChangeSet to publish.",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to confirm the publish action.",
                    },
                },
                "required": ["change_set_id", "confirm"],
            },
            risk_level=RiskLevel.PROPOSE,
            required_permission=_PERM_MAINTAIN,
            owning_module=_OWNING_MODULE,
            timeout_seconds=300.0,
        ),
        _handle_publish,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_exc(exc: BaseException) -> str:
    """Bound exception text so unbounded backends can't leak into results."""
    return str(exc)[:_MAX_EXC_MSG]


def _coerce_run_id(raw) -> "uuid.UUID | None":  # noqa: F821 - forward str
    """Convert the tool context's run_id string into a UUID for the model.

    ``originating_run_id`` is a UUIDField, but the tool context stores run_id
    as an opaque string. Malformed strings become ``None`` rather than
    breaking the change set creation.
    """
    import uuid as _uuid

    if not raw:
        return None
    try:
        return _uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _get_content_operation_service():
    """Return a ContentOperationService instance, or ``None`` if unavailable.

    Different Cauldron installations wire the service via different
    factories. We try the well-known cauldron-admin-content factory first;
    if that fails (either not installed or misconfigured), the caller must
    handle a ``None`` and surface a clear error to the tool result.
    """
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
    """Best-effort: return (item_ids, extra_items) for the given change requests.

    ``item_ids`` — IDs of all items affected by the change requests.  Passed
    to ``build_preview(item_ids_to_include=...)`` so the router includes draft
    versions of pages that already exist in the flatfile store.

    ``extra_items`` — duck-typed ContentItem-like objects constructed directly
    from workspace changeset operations.  They are passed to
    ``build_preview(extra_items=...)`` and always win over router items with the
    same id.  This covers two cases:
    - *create*: the proposed page does not yet exist in the flatfile store so
      the router will not find it; extra_items injects it directly.
    - *update*: the proposed draft data may differ from what the router returns
      for the same item_id; extra_items overrides it with the exact proposed
      content the user reviewed during the proposal step.

    If cauldron-content-operations is not installed, or any lookup fails, we
    return empty lists — the caller falls back to a published-only preview
    rather than crashing the whole workflow.
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

        # Preferred path: pull operations from the workspace changeset.
        ws_id = getattr(cr, "workspace_changeset_id", "")
        if workspace is not None and ws_id:
            try:
                changeset = workspace.load_changeset(ws_id)
                ops_to_process = list(getattr(changeset, "operations", ()) or ())
            except Exception:
                pass

        # Fallback path: operations stored as a JSONField on the change request.
        if not ops_to_process:
            raw_ops = getattr(cr, "operations", None)
            if isinstance(raw_ops, list):
                # Wrap raw dicts as SimpleNamespaces so attribute access works below.
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

            # For updates to existing items, tell the router to include their
            # draft version.  Create operations have no item_id yet so we only
            # add it when present.
            if op_item_id and op_item_id not in seen_item_ids:
                seen_item_ids.add(op_item_id)
                item_ids.append(op_item_id)

            # Delete proposals: skip injection — published page stays visible.
            if op_kind == "delete":
                continue

            if not op_slug:
                continue  # Cannot build a page entry without a slug

            # For create operations there is no item_id yet; use the slug as
            # the deduplication key so the build injects the proposed page.
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
                # If ContentStatus import fails or the namespace is malformed,
                # skip this item; the id is still in item_ids so the router
                # will at least try to find it.
                pass

    return item_ids, extra_items


def _extract_item_ids(content_request_ids: list[str]) -> list[str]:
    """Return item_ids only (no extra_items).  Kept for callers that need only IDs."""
    ids, _ = _extract_draft_items(content_request_ids)
    return ids


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_site_inspect(context, **kwargs):
    from pathlib import Path

    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.inspect",
            success=False,
            message=f"Could not load site build config: {_safe_exc(exc)}",
        )

    output_root = Path(cfg.output_root) if cfg.output_root else None
    live_build_exists = bool(
        output_root and output_root.exists() and any(output_root.iterdir())
    )

    staged_theme_pending = False
    if cfg.theme_root:
        try:
            from cauldron_site_astro.theme import SiteThemeService
            staged_theme_pending = (
                SiteThemeService(cfg.theme_root).get_staged_css() is not None
            )
        except Exception:
            pass

    # Only surface flags — never the filesystem paths themselves.
    return AdminAIToolResult(
        tool_name="site.inspect",
        success=True,
        data={
            "live_build_exists": live_build_exists,
            "staged_theme_pending": staged_theme_pending,
        },
    )


def _handle_stage_theme(context, *, css_content, description="", **kwargs):
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.stage_theme",
            success=False,
            message=f"Could not load site build config: {_safe_exc(exc)}",
        )

    if not cfg.theme_root:
        return AdminAIToolResult(
            tool_name="site.stage_theme",
            success=False,
            message="theme_root is not configured; cannot stage theme.",
        )

    try:
        from cauldron_site_astro.theme import SiteThemeService
        SiteThemeService(cfg.theme_root).stage_css(css_content)
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.stage_theme",
            success=False,
            message=f"Failed to stage theme CSS: {_safe_exc(exc)}",
        )

    return AdminAIToolResult(
        tool_name="site.stage_theme",
        success=True,
        data={
            "staged": True,
            "css_length": len(css_content),
            "description": description,
        },
        message=(
            "Theme CSS staged. Call site.prepare_change_set to build a "
            "preview — the staged CSS is included automatically."
        ),
    )


def _handle_prepare_change_set(
    context,
    *,
    content_request_ids,
    theme_css="",
    description="",
    **kwargs,
):
    from pathlib import Path

    from django.utils import timezone

    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.models import SiteChangeSet

    # ---- Validate inputs ---------------------------------------------------
    if not isinstance(content_request_ids, list) or not content_request_ids:
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            message="content_request_ids must be a non-empty list of strings.",
        )
    content_request_ids = [str(x) for x in content_request_ids]

    # ---- Load build config -------------------------------------------------
    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            message=f"Could not load site build config: {_safe_exc(exc)}",
        )

    if not cfg.previews_root:
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            message="previews_root is not configured; cannot build preview.",
        )

    # ---- Auto-load staged theme CSS if not explicitly supplied ------------
    if not theme_css and cfg.theme_root:
        try:
            from cauldron_site_astro.theme import SiteThemeService
            theme_css = SiteThemeService(cfg.theme_root).get_staged_css() or ""
        except Exception:
            pass

    # ---- Create the SiteChangeSet in 'preparing' state --------------------
    # _extract_draft_items returns (item_ids, extra_items):
    # - item_ids: used for scoping (include existing router drafts with these ids)
    # - extra_items: the proposed item content extracted directly from workspace
    #   changesets, injected into the preview so new pages (not yet in the
    #   flatfile store) and edited pages (override router version with proposed
    #   content) appear exactly as the operator reviewed them.
    affected_item_ids, draft_extra_items = _extract_draft_items(content_request_ids)
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.PREPARING,
        content_request_ids=content_request_ids,
        staged_theme_css=theme_css or "",
        originating_run_id=_coerce_run_id(getattr(context, "run_id", None)),
        creator=getattr(context, "actor", None),
        affected_item_ids=affected_item_ids,
    )

    # ---- Build the scoped preview -----------------------------------------
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
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            data={"change_set_id": str(cs.id)},
            message=f"Preview build raised an exception: {_safe_exc(exc)}",
        )

    if not result.ok:
        cs.status = SiteChangeSet.PREVIEW_FAILED
        cs.preview_dir = str(cs.id)
        cs.save(update_fields=["status", "preview_dir", "updated_at"])
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            data={
                "change_set_id": str(cs.id),
                "build_log": (result.build_log or "")[-_MAX_BUILD_LOG_TAIL:],
            },
            message=f"Preview build failed: {_safe_exc(Exception(result.error or ''))}",
        )

    cs.status = SiteChangeSet.DRAFT_READY
    cs.preview_dir = str(cs.id)  # relative path beneath previews_root
    cs.draft_ready_at = timezone.now()
    cs.save(update_fields=[
        "status", "preview_dir", "draft_ready_at", "updated_at",
    ])

    return AdminAIToolResult(
        tool_name="site.prepare_change_set",
        success=True,
        data={
            "change_set_id": str(cs.id),
            "pages_built": result.pages_built,
            "preview_url": cs.get_preview_url(),
            "description": description,
        },
        message=(
            f"Change set {cs.id} is draft_ready ({result.pages_built} page(s)). "
            f"Review at the returned preview_url, then call "
            f"site.publish(change_set_id={str(cs.id)!r}, confirm=true) to go live."
        ),
    )


def _handle_inspect_preview(context, *, change_set_id, **kwargs):
    from pathlib import Path

    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.models import SiteChangeSet

    try:
        cs = SiteChangeSet.objects.get(id=change_set_id)
    except (SiteChangeSet.DoesNotExist, ValueError):
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message=f"Change set {change_set_id!r} not found.",
        )

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message=f"Could not load site build config: {_safe_exc(exc)}",
        )

    pages_built = 0
    if cfg.previews_root and cs.preview_dir:
        preview_dir = Path(cfg.previews_root) / cs.preview_dir
        if preview_dir.exists() and preview_dir.is_dir():
            pages_built = sum(1 for _ in preview_dir.rglob("*.html"))

    return AdminAIToolResult(
        tool_name="site.inspect_preview",
        success=True,
        data={
            "change_set_id": str(cs.id),
            "status": cs.status,
            "pages_built": pages_built,
            "preview_url": cs.get_preview_url(),
            "created_at": cs.created_at.isoformat() if cs.created_at else "",
        },
    )


def _handle_publish(context, *, change_set_id, confirm, **kwargs):
    """Publish a draft-ready SiteChangeSet to the live site.

    Execution order and rollback guarantees
    ----------------------------------------
    1. Validate all content requests (read-only pre-flight).
    2. Build via build_preview with draft items — no DB or FS mutations yet.
    3. Snapshot current active CSS (in memory).
    4. Promote output to live output_root — keeping the previous output as a
       rollback snapshot (promote_output_with_backup).
    5. Promote staged CSS to active.css.
       On failure: restore output from snapshot → PUBLISH_FAILED.
    6. Apply content requests inside a single transaction.atomic() block.
       On failure: transaction rolls back; restore output from snapshot;
       restore active.css from in-memory snapshot → PUBLISH_FAILED.
    7. Discard output snapshot (commit).  Mark PUBLISHED.

    This ordering ensures:
    - A build failure leaves the content store unchanged (step 2 first).
    - A CSS failure leaves content unchanged and the previous output live.
    - A DB failure leaves the previous output and previous CSS live;
      the transaction rollback guarantees no partial content applies.
    - PUBLISH_FAILED change sets may be retried (all FS state is restored).
    """
    import shutil
    import tempfile

    from django.db import transaction
    from django.utils import timezone

    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.models import SiteChangeSet

    if not confirm:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message="Publish not confirmed. Set confirm=true to proceed.",
        )

    try:
        cs = SiteChangeSet.objects.get(id=change_set_id)
    except (SiteChangeSet.DoesNotExist, ValueError):
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message=f"Change set {change_set_id!r} not found.",
        )

    # Allow retry from publish_failed: all FS state is restored on each failure.
    if cs.status not in (SiteChangeSet.DRAFT_READY, SiteChangeSet.PUBLISH_FAILED):
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            data={"change_set_id": str(cs.id), "status": cs.status},
            message=(
                f"Change set is in status {cs.status!r}; "
                f"only draft_ready or publish_failed change sets can be published."
            ),
        )

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message=f"Could not load site build config: {_safe_exc(exc)}",
        )

    cs.status = SiteChangeSet.PUBLISHING
    cs.save(update_fields=["status", "updated_at"])

    # ---- Step 1: Validate all content requests (read-only pre-flight) ------
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
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
                message="Content operations service is unavailable; cannot apply changes.",
            )

        try:
            v = content_service.validate_change_request(req_id, user=context.actor)
        except Exception as exc:
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = {
                "error": f"validate raised: {_safe_exc(exc)}",
                "applied": [],
                "failed_request_id": req_id,
            }
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
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
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
                message=f"Validation of {req_id!r} failed: {err_msg[:_MAX_EXC_MSG]}",
            )

        validated[req_id] = v

    # ---- Step 2: Build (no DB or FS mutations) -----------------------------
    # Extract proposed item content from workspace changesets so that new pages
    # (not yet in the flatfile store) appear in the build with the exact content
    # the operator reviewed.  The content requests are applied in Step 6 AFTER
    # the build, so without extra_items those pages would be absent from the
    # published output.
    _, publish_extra_items = _extract_draft_items(cs.content_request_ids or [])

    tmp_build_dir = tempfile.mkdtemp(prefix="cauldron_pub_")
    output_snapshot: "Path | None" = None  # set after promote_output_with_backup

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
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
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
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={
                    "change_set_id": str(cs.id),
                    "build_log": (result.build_log or "")[-_MAX_BUILD_LOG_TAIL:],
                },
                message=f"Publish build failed: {(result.error or '')[:_MAX_EXC_MSG]}",
            )

        # ---- Step 3: Snapshot current active CSS (before any FS mutation) --
        theme_svc = None
        prev_active_css = ""
        if cs.staged_theme_css and cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_svc = SiteThemeService(cfg.theme_root)
                prev_active_css = theme_svc.get_active_css()
            except Exception:
                pass  # Non-fatal; snapshot is "" if unavailable

        # ---- Step 4: Promote output with rollback snapshot -----------------
        try:
            output_snapshot = svc.promote_output_with_backup(tmp_build_dir)
        except Exception as exc:
            # No DB changes, no CSS changes; FS unchanged (promotion failed before swap).
            cs.status = SiteChangeSet.PUBLISH_FAILED
            cs.publish_build_result = {
                "error": f"output promotion failed: {_safe_exc(exc)}",
                "applied": [],
            }
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
                message=f"Output promotion failed: {_safe_exc(exc)}",
            )

        # ---- Step 5: Promote CSS -------------------------------------------
        if theme_svc is not None:
            try:
                theme_svc.stage_css(cs.staged_theme_css)
                theme_svc.promote_staged()
            except Exception as exc:
                # CSS failed. Restore output. No DB changes.
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
                return AdminAIToolResult(
                    tool_name="site.publish",
                    success=False,
                    data={"change_set_id": str(cs.id)},
                    message=f"Theme promotion failed: {_safe_exc(exc)}",
                )

        # ---- Step 6: Apply content requests inside a single transaction ----
        # All-or-nothing: any failure raises out of the atomic block, causing
        # an automatic rollback. No partial content state is ever committed.
        applied_ids: list[str] = []
        _apply_err: str = ""

        try:
            with transaction.atomic():
                for req_id in cs.content_request_ids or ():
                    v = validated[req_id]
                    try:
                        a = content_service.apply_change_request(
                            req_id,
                            user=context.actor,
                            expected_version=getattr(v, "request_version", 0),
                        )
                    except Exception as exc:
                        _apply_err = f"apply raised for {req_id!r}: {_safe_exc(exc)}"
                        raise  # Propagates out of atomic(); triggers rollback
                    if not getattr(a, "ok", False):
                        err = getattr(a, "error", None)
                        err_msg = getattr(err, "message", "apply failed")[:_MAX_EXC_MSG]
                        _apply_err = f"apply failed for {req_id!r}: {err_msg}"
                        raise RuntimeError(_apply_err)  # Triggers rollback
                    applied_ids.append(req_id)
        except Exception:
            # Transaction rolled back: no content changes committed.
            # Restore output and CSS to their pre-publish state.
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
                # applied_ids here are NOT committed (transaction rolled back)
                "error": _apply_err or "DB apply transaction failed",
                "applied": [],
            }
            cs.save(update_fields=["status", "publish_build_result", "updated_at"])
            return AdminAIToolResult(
                tool_name="site.publish",
                success=False,
                data={"change_set_id": str(cs.id)},
                message=_apply_err or "Content apply transaction failed; all changes rolled back.",
            )

        # ---- Step 7: Commit — discard snapshot and mark PUBLISHED ----------
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

        return AdminAIToolResult(
            tool_name="site.publish",
            success=True,
            data={
                "change_set_id": str(cs.id),
                "published": True,
                "pages_built": result.pages_built,
                "live_url": "/",
            },
            message=(
                f"Change set {cs.id} published successfully "
                f"({result.pages_built} page(s))."
            ),
        )

    finally:
        shutil.rmtree(tmp_build_dir, ignore_errors=True)
        # Safety net: clean up backup if an unexpected exception bypassed normal paths
        if output_snapshot is not None:
            try:
                svc.discard_output_backup(output_snapshot)
            except Exception:
                pass


def _handle_verify_root(context, **kwargs):
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None

    output_root = None
    try:
        svc = get_build_service()
        cfg = svc._config
        output_root = cfg.output_root or None
    except Exception:
        pass

    content_service = _get_content_operation_service()

    try:
        diag = run_site_diagnostics(
            actor=context.actor,
            service=content_service,
            output_root=output_root,
        )
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.verify_root",
            success=False,
            message=f"Diagnostics raised an unexpected error: {_safe_exc(exc)}",
        )

    return AdminAIToolResult(
        tool_name="site.verify_root",
        success=True,
        data=diag,
    )


def _handle_propose_homepage(
    context,
    *,
    title,
    body,
    navigation_title="",
    summary="",
    seo_title="",
    meta_description="",
    **kwargs,
):
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None

    svc = _get_content_operation_service()
    if svc is None:
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message="Content operations service is unavailable.",
        )

    try:
        from cauldron_content.homepage import (
            HOMEPAGE_ITEM_ID,
            HOMEPAGE_COLLECTION,
            build_homepage_operation,
        )
        from cauldron_content.contracts import ContentStatus
    except ImportError:
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message="cauldron-content package is required but not installed.",
        )

    # Determine create vs. update by looking up the current homepage.
    kind = "create"
    expected_hash = ""
    try:
        existing = svc.get_item(
            HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION, user=context.actor
        )
        if existing is not None:
            kind = "update"
            expected_hash = getattr(existing, "hash", "") or ""
    except Exception:
        pass

    op = build_homepage_operation(
        kind=kind,
        status=ContentStatus.DRAFT,
        title=title,
        body=body,
        expected_hash=expected_hash,
        navigation_title=navigation_title,
        summary=summary,
        seo_title=seo_title,
        meta_description=meta_description,
    )

    try:
        result = svc.create_change_request(
            user=context.actor,
            operations=[op],
            provider_name="cauldron_site_astro",
            description=f"Homepage {kind}: {title[:80]}",
        )
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message=f"Failed to create change request: {_safe_exc(exc)}",
        )

    if not getattr(result, "ok", False):
        err = getattr(result, "error", None)
        msg = getattr(err, "message", "proposal failed")
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message=f"Homepage proposal failed: {msg[:_MAX_EXC_MSG]}",
        )

    cs_id = getattr(result, "request_id", "") or ""
    return AdminAIToolResult(
        tool_name="site.propose_homepage",
        success=True,
        data={
            "cs_id": str(cs_id),
            "status": "proposed",
            "kind": kind,
        },
        message=(
            f"Homepage {kind} proposed (cs_id={cs_id!r}). "
            "Pass cs_id to site.prepare_change_set to build a preview."
        ),
    )
