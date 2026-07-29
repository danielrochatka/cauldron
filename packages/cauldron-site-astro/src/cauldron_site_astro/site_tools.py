"""Admin AI site tools for cauldron-site-astro.

Provides 5 tools that let the Admin AI authoring workflow inspect and
initiate site builds / previews without granting unrestricted write access:

1. site.inspect      — read current build status (READ_ONLY)
2. site.stage_theme  — stage a CSS theme for the next publish (PROPOSE)
3. site.prepare_preview — run a preview build with draft content (PROPOSE)
4. site.inspect_preview — read a preview build's status (READ_ONLY)
5. site.publish      — promote a preview to the live site (MAINTENANCE)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cauldron_ai_admin.tools import AdminAIToolRegistry


def register(registry: "AdminAIToolRegistry") -> None:
    """Register all site tools into *registry*."""
    from cauldron_ai_admin.tools import AdminAIToolDefinition, RiskLevel

    _OWNING_MODULE = "cauldron.site.astro"
    _PERM_VIEW = "cauldron_content_operations.view_published_content"
    _PERM_PROPOSE = "cauldron_content_operations.propose_content_changes"
    _PERM_MAINTAIN = "cauldron_content_operations.apply_content_changes"

    # ------------------------------------------------------------------
    # 1. site.inspect
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.inspect",
            version="1.0",
            description=(
                "Inspect the current public site build status: whether a live "
                "build exists, the output directory, and whether a staged theme "
                "CSS is pending. Read-only."
            ),
            argument_schema={"type": "object", "properties": {}, "required": []},
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_VIEW,
            owning_module=_OWNING_MODULE,
        ),
        _handle_site_inspect,
    )

    # ------------------------------------------------------------------
    # 2. site.stage_theme
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
    # 3. site.prepare_preview
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.prepare_preview",
            version="1.0",
            description=(
                "Run a preview Astro build that includes draft pages and the "
                "staged theme (if any). The result is written to a dedicated "
                "preview directory and does NOT touch the live site. "
                "Returns a preview_id and the output directory path."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Optional description for this preview.",
                    },
                },
                "required": [],
            },
            risk_level=RiskLevel.PROPOSE,
            required_permission=_PERM_PROPOSE,
            owning_module=_OWNING_MODULE,
            timeout_seconds=180.0,
        ),
        _handle_prepare_preview,
    )

    # ------------------------------------------------------------------
    # 4. site.inspect_preview
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.inspect_preview",
            version="1.0",
            description=(
                "Inspect a previously prepared preview build. Returns its "
                "output directory path and the list of pages it contains."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "preview_id": {
                        "type": "string",
                        "description": "UUID string returned by site.prepare_preview.",
                    },
                },
                "required": ["preview_id"],
            },
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_VIEW,
            owning_module=_OWNING_MODULE,
        ),
        _handle_inspect_preview,
    )

    # ------------------------------------------------------------------
    # 5. site.publish
    # ------------------------------------------------------------------
    registry.register(
        AdminAIToolDefinition(
            name="site.publish",
            version="1.0",
            description=(
                "Trigger a full public-site rebuild and promote it to the live "
                "output directory. This is a deliberate publish action that "
                "replaces the live site. Requires MAINTENANCE-level permission. "
                "If a staged theme is present it is promoted to active first."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to confirm the publish action.",
                    },
                },
                "required": ["confirm"],
            },
            risk_level=RiskLevel.MAINTENANCE,
            required_permission=_PERM_MAINTAIN,
            owning_module=_OWNING_MODULE,
            timeout_seconds=300.0,
        ),
        _handle_publish,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_site_inspect(context, **kwargs):
    from pathlib import Path

    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_site_astro.service import get_build_service

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.inspect",
            success=False,
            message=f"Could not load site build config: {exc}",
        )

    output_root = Path(cfg.output_root) if cfg.output_root else None
    live_build_exists = bool(
        output_root and output_root.exists() and any(output_root.iterdir())
    )

    staged_theme_pending = False
    if cfg.theme_root:
        try:
            from cauldron_site_astro.theme import SiteThemeService
            staged_theme_pending = SiteThemeService(cfg.theme_root).get_staged_css() is not None
        except Exception:
            pass

    return AdminAIToolResult(
        tool_name="site.inspect",
        success=True,
        data={
            "live_build_exists": live_build_exists,
            "output_root": cfg.output_root or "",
            "frontend_root": cfg.frontend_root or "",
            "theme_root": cfg.theme_root or "",
            "previews_root": cfg.previews_root or "",
            "staged_theme_pending": staged_theme_pending,
        },
    )


def _handle_stage_theme(context, *, css_content, description="", **kwargs):
    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_site_astro.service import get_build_service

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.stage_theme",
            success=False,
            message=f"Could not load site build config: {exc}",
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
            message=f"Failed to stage theme CSS: {exc}",
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
            "Theme CSS staged. Call site.prepare_preview to verify it, "
            "then site.publish to promote it to the live site."
        ),
    )


def _handle_prepare_preview(context, *, description="", **kwargs):
    import uuid
    from pathlib import Path

    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_site_astro.service import get_build_service

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.prepare_preview",
            success=False,
            message=f"Could not load site build config: {exc}",
        )

    if not cfg.previews_root:
        return AdminAIToolResult(
            tool_name="site.prepare_preview",
            success=False,
            message="previews_root is not configured; cannot build preview.",
        )

    preview_id = str(uuid.uuid4())
    output_dir = Path(cfg.previews_root) / preview_id

    # Use staged theme CSS if available
    theme_css = ""
    if cfg.theme_root:
        try:
            from cauldron_site_astro.theme import SiteThemeService
            staged = SiteThemeService(cfg.theme_root).get_staged_css()
            if staged is not None:
                theme_css = staged
        except Exception:
            pass

    try:
        result = svc.build_preview(
            output_dir=output_dir,
            theme_css=theme_css,
        )
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.prepare_preview",
            success=False,
            message=f"Preview build raised an exception: {exc}",
        )

    if not result.ok:
        return AdminAIToolResult(
            tool_name="site.prepare_preview",
            success=False,
            data={"preview_id": preview_id, "build_log": result.build_log[-1000:]},
            message=f"Preview build failed: {result.error}",
        )

    return AdminAIToolResult(
        tool_name="site.prepare_preview",
        success=True,
        data={
            "preview_id": preview_id,
            "output_dir": str(output_dir),
            "pages_built": result.pages_built,
            "description": description,
        },
        message=(
            f"Preview built with {result.pages_built} page(s). "
            f"Use site.inspect_preview with preview_id={preview_id!r} to review, "
            "then site.publish to go live."
        ),
    )


def _handle_inspect_preview(context, *, preview_id, **kwargs):
    from pathlib import Path

    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_site_astro.service import get_build_service

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message=f"Could not load site build config: {exc}",
        )

    if not cfg.previews_root:
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message="previews_root is not configured.",
        )

    preview_dir = Path(cfg.previews_root) / preview_id
    if not preview_dir.exists():
        return AdminAIToolResult(
            tool_name="site.inspect_preview",
            success=False,
            message=f"Preview {preview_id!r} not found.",
        )

    # Collect .html files as a proxy for built pages
    html_files = sorted(str(p.relative_to(preview_dir)) for p in preview_dir.rglob("*.html"))

    return AdminAIToolResult(
        tool_name="site.inspect_preview",
        success=True,
        data={
            "preview_id": preview_id,
            "output_dir": str(preview_dir),
            "html_files": html_files[:50],
            "html_file_count": len(html_files),
            "truncated": len(html_files) > 50,
        },
    )


def _handle_publish(context, *, confirm, **kwargs):
    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_site_astro.service import get_build_service

    if not confirm:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message="Publish not confirmed. Set confirm=true to proceed.",
        )

    try:
        svc = get_build_service()
        cfg = svc._config
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message=f"Could not load site build config: {exc}",
        )

    # Promote staged theme to active before building
    if cfg.theme_root:
        try:
            from cauldron_site_astro.theme import SiteThemeService
            SiteThemeService(cfg.theme_root).promote_staged()
        except Exception:
            pass  # Non-fatal: build will use existing active theme

    try:
        result = svc.build()
    except Exception as exc:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            message=f"Publish build raised an exception: {exc}",
        )

    if not result.ok:
        return AdminAIToolResult(
            tool_name="site.publish",
            success=False,
            data={"build_log": result.build_log[-1000:]},
            message=f"Publish build failed: {result.error}",
        )

    return AdminAIToolResult(
        tool_name="site.publish",
        success=True,
        data={
            "pages_built": result.pages_built,
            "output_dir": result.output_dir,
        },
        message=f"Site published successfully with {result.pages_built} page(s).",
    )
