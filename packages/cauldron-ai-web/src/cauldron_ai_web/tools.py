"""Admin AI tools for web URL inspection."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .fetcher import UnsafeUrlError, UrlFetchError, get_fetcher
from .analyzer import analyze_css, analyze_html

if TYPE_CHECKING:
    from cauldron_ai_admin.tools import AdminAIToolRegistry

logger = logging.getLogger(__name__)

OWNING_MODULE = "cauldron.ai.web"
# Web inspection is gated by the Admin AI use permission, which is created
# by cauldron_ai_admin migrations (AdminAIRun.Meta.permissions). This tool
# is only registered when cauldron_ai_admin is installed, so the permission
# is always available when the tool is reachable.
_PERM_INSPECT = "cauldron_ai_admin.use_admin_ai"


def _handle_web_inspect_url(context, *, url: str):
    try:
        from cauldron_ai_admin.tools import AdminAIToolError, AdminAIToolResult
    except ImportError:
        return None

    if not url or not isinstance(url, str):
        return AdminAIToolError(
            tool_name="web.inspect_url",
            error_code="tool.invalid_arguments",
            message="url must be a non-empty string.",
        )

    fetcher = get_fetcher()

    try:
        result = fetcher.fetch(url)
    except UnsafeUrlError as exc:
        return AdminAIToolError(
            tool_name="web.inspect_url",
            error_code="web.unsafe_url",
            message=str(exc),
        )
    except UrlFetchError as exc:
        return AdminAIToolError(
            tool_name="web.inspect_url",
            error_code="web.fetch_failed",
            message=str(exc),
        )
    except Exception:
        logger.exception("Unexpected error fetching URL %r", url)
        return AdminAIToolError(
            tool_name="web.inspect_url",
            error_code="tool.internal_error",
            message="Could not fetch the URL.",
        )

    try:
        html_content = result.body_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_content = ""

    try:
        characteristics = analyze_html(html_content, base_url=result.final_url)
    except Exception:
        logger.exception("HTML analysis failed for %r", url)
        characteristics = None

    # Optionally fetch and analyze the first stylesheet
    if characteristics and characteristics.stylesheet_urls:
        css_url = characteristics.stylesheet_urls[0]
        if css_url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(result.final_url)
            css_url = f"{parsed.scheme}://{parsed.netloc}{css_url}"
        elif not css_url.startswith(("http://", "https://")):
            # Relative URL — resolve against final_url base
            from urllib.parse import urljoin
            css_url = urljoin(result.final_url, css_url)

        try:
            css_result = fetcher.fetch(css_url)
            css_content = css_result.body_bytes.decode("utf-8", errors="replace")
            characteristics = analyze_css(css_content, existing=characteristics)
        except Exception:
            logger.debug("CSS fetch/analysis failed for %r — continuing without CSS data", css_url)

    if characteristics is None:
        return AdminAIToolError(
            tool_name="web.inspect_url",
            error_code="web.analysis_failed",
            message="Could not analyse the fetched page.",
        )

    data = {
        "url": url,
        "final_url": result.final_url,
        "status_code": result.status_code,
        "title": characteristics.title,
        "headings": list(characteristics.headings),
        "nav_items": list(characteristics.nav_items),
        "font_families": list(characteristics.font_families),
        "color_hints": list(characteristics.color_hints),
        "background_is_light": characteristics.background_is_light,
        "uses_cards": characteristics.uses_cards,
        "border_radius_hint": characteristics.border_radius_hint,
        "spacing_hint": characteristics.spacing_hint,
        "visible_text_summary": characteristics.visible_text_summary,
        "css_variables": characteristics.css_variables,
    }

    return AdminAIToolResult(
        tool_name="web.inspect_url",
        success=True,
        data=data,
        message=f"URL '{url}' inspected successfully.",
    )


def register(registry: "AdminAIToolRegistry") -> None:
    try:
        from cauldron_ai_admin.tools import AdminAIToolDefinition, RiskLevel
    except ImportError:
        return

    registry.register(
        AdminAIToolDefinition(
            name="web.inspect_url",
            version="1.0",
            description=(
                "Fetch a public URL and extract design characteristics: title, headings, "
                "navigation items, font families, colour hints, CSS custom properties, "
                "layout patterns (cards, border radius, spacing), and a visible text "
                "summary. Only public HTTP/HTTPS URLs are allowed — private/internal "
                "addresses are blocked by SSRF protection."
            ),
            argument_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Public HTTP or HTTPS URL to inspect.",
                    },
                },
                "required": ["url"],
            },
            risk_level=RiskLevel.READ_ONLY,
            required_permission=_PERM_INSPECT,
            owning_module=OWNING_MODULE,
            timeout_seconds=30.0,
            max_output_bytes=65536,
        ),
        _handle_web_inspect_url,
    )
