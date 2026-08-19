"""Django system checks for cauldron_ai_site_builder.

Verifies that the required Admin AI tools are registered when
cauldron-ai-admin is installed.
"""
from __future__ import annotations

from django.core.checks import Warning, register

_REQUIRED_TOOLS = ("attachments.read", "web.inspect_url")


@register()
def check_required_tools_registered(app_configs, **kwargs):
    warnings = []

    try:
        from cauldron_ai_admin.tools import get_tool_registry
    except ImportError:
        # cauldron-ai-admin not installed — skip
        return []

    try:
        registry = get_tool_registry()
        registered_names = {t.name for t in registry.all_definitions()}
    except Exception:
        warnings.append(
            Warning(
                "Could not query the Admin AI tool registry during system checks.",
                id="cauldron_ai_site_builder.W001",
            )
        )
        return warnings

    for tool_name in _REQUIRED_TOOLS:
        if tool_name not in registered_names:
            warnings.append(
                Warning(
                    f"Admin AI tool '{tool_name}' is not registered. "
                    f"The site-builder MVP requires this tool. "
                    f"Ensure the owning package is installed and its Django app is in INSTALLED_APPS.",
                    hint=f"Add the package that provides '{tool_name}' to INSTALLED_APPS.",
                    id="cauldron_ai_site_builder.W002",
                )
            )

    return warnings
