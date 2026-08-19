"""Prompt templates for cauldron_ai_web tools."""
from __future__ import annotations

_OWNING_MODULE = "cauldron.ai.web"


def register_tool_prompts() -> None:
    try:
        from cauldron_ai.prompt_templates import AIToolPromptTemplate, get_prompt_template_registry
    except ImportError:
        return

    registry = get_prompt_template_registry()
    registry.register_tool_template(
        AIToolPromptTemplate(
            tool_name="web.inspect_url",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Fetch a public reference website and extract its design characteristics "
                "— typography, colour palette, layout patterns, navigation structure, "
                "and visible text. Use this to understand a client's existing brand or "
                "reference site before proposing site styles or content."
            ),
            supported_tasks=(
                "reference site analysis",
                "design characteristic extraction",
                "brand research for site-builder workflow",
            ),
            required_permission="cauldron_ai_web.inspect_url",
            risk_level="READ_ONLY",
            read_scope=(
                "Publicly accessible HTTP/HTTPS URL only. "
                "Extracts: page title, headings (h1–h4), nav items, font families, "
                "colour hints, CSS variables, border-radius and spacing hints, "
                "uses_cards flag, visible text summary (up to 5000 characters). "
                "Private/internal IP addresses and localhost are blocked."
            ),
            write_scope="None",
            preconditions=(
                "Actor has cauldron_ai_web.inspect_url permission.",
                "URL must use http or https scheme.",
                "URL must resolve to a public IP address.",
            ),
            input_expectations=(
                "url: A public HTTP or HTTPS URL string. "
                "Must be the full URL including scheme, e.g. https://example.com. "
                "Do not pass file:// or other schemes."
            ),
            result_behavior=(
                "On success: returns data.title, data.headings, data.nav_items, "
                "data.font_families, data.color_hints, data.background_is_light (bool|null), "
                "data.uses_cards, data.border_radius_hint, data.spacing_hint, "
                "data.visible_text_summary, data.css_variables, "
                "data.final_url (after redirects), data.status_code. "
                "On failure: error codes web.unsafe_url (SSRF block), "
                "web.fetch_failed (network/HTTP error), web.analysis_failed."
            ),
            approval_requirements="None required (READ_ONLY)",
            clarification_behavior=(
                "If the user provides a URL without a scheme, prepend https://. "
                "If the URL appears to be internal (localhost, 192.168.x.x, etc.) "
                "explain that internal addresses cannot be fetched and ask for a "
                "public URL."
            ),
            refusal_behavior=(
                "Do not attempt to fetch file://, ftp://, or other non-HTTP schemes. "
                "Do not fetch URLs the user describes as internal, staging, or "
                "behind authentication — those will fail with SSRF or fetch errors."
            ),
            error_guidance=(
                "web.unsafe_url: The URL targets a private/internal address. "
                "Tell the user only public URLs can be inspected. "
                "web.fetch_failed: The URL could not be reached — verify it is correct "
                "and publicly accessible, then retry. "
                "web.analysis_failed: The page was fetched but could not be parsed; "
                "the site may use client-side rendering. Report partial results if any "
                "were returned."
            ),
            positive_examples=(
                "User says 'my site is at https://example.com, match its style' → "
                "call web.inspect_url → use font_families and color_hints to inform "
                "the CSS proposal.",
                "User provides a competitor URL as design reference → inspect it and "
                "summarise the typography and colour palette.",
            ),
            boundary_examples=(
                "Do not use web.inspect_url to check Cauldron's own admin URLs — "
                "use site.inspect instead.",
                "Do not fetch URLs containing authentication tokens or session cookies "
                "in the query string.",
            ),
        )
    )
