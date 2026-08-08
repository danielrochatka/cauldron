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
                "already exists (including drafts), then creates a content "
                "change request. Returns cs_id for use with site.prepare_change_set."
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
                    "canonical_url": {
                        "type": "string",
                        "description": "Canonical URL for the homepage.",
                    },
                    "robots_index": {
                        "type": "boolean",
                        "description": "Whether search engines should index this page.",
                    },
                    "robots_follow": {
                        "type": "boolean",
                        "description": "Whether search engines should follow links on this page.",
                    },
                    "social_title": {
                        "type": "string",
                        "description": "Open Graph / social media title override.",
                    },
                    "social_description": {
                        "type": "string",
                        "description": "Open Graph / social media description.",
                    },
                    "social_image": {
                        "type": "string",
                        "description": "Open Graph / social media image URL.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description for the change request.",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": "Optional key to deduplicate proposals.",
                    },
                },
                "required": ["title", "body"],
                "additionalProperties": False,
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
    """Thin adapter — validates tool-specific args and delegates to the service."""
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.publication_service import get_publication_service

    result = get_publication_service().prepare(
        actor=getattr(context, "actor", None),
        content_request_ids=content_request_ids if isinstance(content_request_ids, list) else [],
        originating_run_id=getattr(context, "run_id", None),
        description=description,
        staged_theme_css=theme_css,
    )

    if not result.ok:
        data: dict = {}
        if result.change_set_id:
            data["change_set_id"] = result.change_set_id
        if result.build_log_tail:
            data["build_log"] = result.build_log_tail
        return AdminAIToolResult(
            tool_name="site.prepare_change_set",
            success=False,
            data=data or None,
            message=result.message,
        )

    return AdminAIToolResult(
        tool_name="site.prepare_change_set",
        success=True,
        data={
            "change_set_id": result.change_set_id,
            "pages_built": result.pages_built,
            "preview_url": result.preview_url,
            "description": description,
        },
        message=(
            f"Change set {result.change_set_id} is draft_ready "
            f"({result.pages_built} page(s)). Review at the returned preview_url, "
            f"then call site.publish(change_set_id={result.change_set_id!r}, "
            f"confirm=true) to go live."
        ),
    )


def _handle_inspect_preview(context, *, change_set_id, **kwargs):
    """Thin adapter — delegates to :class:`SiteChangeSetService`."""
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.publication_service import get_publication_service

    result = get_publication_service().inspect(change_set_id)

    if not result.ok:
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message=result.message,
        )

    return AdminAIToolResult(
        tool_name="site.inspect_preview",
        success=True,
        data={
            "change_set_id": result.change_set_id,
            "status": result.status,
            "pages_built": result.pages_built,
            "preview_url": result.preview_url,
            "created_at": result.created_at,
        },
    )


def _handle_publish(context, *, change_set_id, confirm, **kwargs):
    """Thin adapter — delegates to :class:`SiteChangeSetService`.

    Validates the ``confirm`` guard, then hands the change-set publish to the
    domain service. The service reproduces the original 7-step atomic
    transaction (validate → build → snapshot output → promote output →
    promote CSS → apply content in transaction.atomic() → discard snapshot).
    """
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None
    from cauldron_site_astro.publication_service import get_publication_service

    if not confirm:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message="Publish not confirmed. Set confirm=true to proceed.",
        )

    result = get_publication_service().publish(
        actor=getattr(context, "actor", None),
        change_set_id=change_set_id,
    )

    if not result.ok:
        data: dict = {}
        if result.change_set_id:
            data["change_set_id"] = result.change_set_id
        if result.status:
            data["status"] = result.status
        if result.build_log_tail:
            data["build_log"] = result.build_log_tail
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            data=data or None,
            message=result.message,
        )

    return AdminAIToolResult(
        tool_name="site.publish",
        success=True,
        data={
            "change_set_id": result.change_set_id,
            "published": True,
            "pages_built": result.pages_built,
            "live_url": result.live_url or "/",
        },
        message=(
            f"Change set {result.change_set_id} published successfully "
            f"({result.pages_built} page(s))."
        ),
    )


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
    canonical_url="",
    robots_index=True,
    robots_follow=True,
    social_title="",
    social_description="",
    social_image="",
    description="",
    idempotency_key="",
    **kwargs,
):
    try:
        from cauldron_ai_admin.tools import AdminAIToolResult
    except ImportError:
        return None

    _PERM_VIEW = "cauldron_content_operations.view_published_content"
    _PERM_DRAFT = "cauldron_content_operations.view_draft_content"

    actor = context.actor

    # Check additional read permissions; tool registry only enforces propose_content_changes.
    if not getattr(actor, "has_perm", lambda _: False)(_PERM_VIEW):
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message=(
                f"Actor lacks {_PERM_VIEW!r}; cannot safely determine homepage state."
            ),
        )
    if not getattr(actor, "has_perm", lambda _: False)(_PERM_DRAFT):
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message=(
                f"Actor lacks {_PERM_DRAFT!r}; cannot safely determine homepage state."
            ),
        )

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

    # Determine create vs. update by looking up the current homepage (drafts included).
    # Fail closed: any lookup error is a tool failure — never silently fall through to create.
    kind = "create"
    expected_hash = ""
    try:
        existing = svc.get_item(
            HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION, user=actor, include_drafts=True
        )
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.propose_homepage",
            success=False,
            message=f"Homepage lookup failed: {_safe_exc(exc)}",
        )
    if existing is not None:
        kind = "update"
        expected_hash = getattr(existing, "hash", "") or ""

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
        canonical_url=canonical_url,
        robots_index=robots_index,
        robots_follow=robots_follow,
        social_title=social_title,
        social_description=social_description,
        social_image=social_image,
    )

    change_request_description = description or f"Homepage {kind}: {title[:80]}"

    try:
        result = svc.create_change_request(
            user=actor,
            operations=[op],
            provider_name="",
            description=change_request_description,
            idempotency_key=idempotency_key,
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
