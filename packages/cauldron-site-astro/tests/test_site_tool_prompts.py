"""Upgrade-path tests for built-in site tool prompt templates.

Verifies:
  1. All 5 site tool templates are registered after app startup.
  2. Re-registration is a no-op (idempotent).
  3. E017 passes (no missing templates for registered tools).
  4. E021 passes (required_permission matches tool definition).
"""
from __future__ import annotations

import pytest


EXPECTED_SITE_TOOLS = {
    "site.inspect",
    "site.stage_theme",
    "site.prepare_change_set",
    "site.inspect_preview",
    "site.publish",
}


def test_all_site_tool_templates_registered():
    """All 5 site tools must have a prompt template after app startup."""
    from cauldron_ai.prompt_templates import get_prompt_template_registry
    registry = get_prompt_template_registry()
    for tool_name in EXPECTED_SITE_TOOLS:
        tmpl = registry.get_tool_template(tool_name)
        assert tmpl is not None, f"No prompt template registered for {tool_name!r}"
        assert tmpl.tool_name == tool_name


def test_templates_have_correct_owning_module():
    from cauldron_ai.prompt_templates import get_prompt_template_registry
    registry = get_prompt_template_registry()
    for tool_name in EXPECTED_SITE_TOOLS:
        tmpl = registry.get_tool_template(tool_name)
        assert tmpl.owning_module == "cauldron.site.astro", (
            f"{tool_name}: expected owning_module 'cauldron.site.astro', "
            f"got {tmpl.owning_module!r}"
        )


def test_template_risk_levels():
    """Verify risk levels match the tool definitions."""
    from cauldron_ai.prompt_templates import get_prompt_template_registry
    registry = get_prompt_template_registry()

    expected_risk = {
        "site.inspect": "READ_ONLY",
        "site.stage_theme": "PROPOSE",
        "site.prepare_change_set": "PROPOSE",
        "site.inspect_preview": "READ_ONLY",
        "site.publish": "PROPOSE",
    }
    for tool_name, risk in expected_risk.items():
        tmpl = registry.get_tool_template(tool_name)
        assert tmpl is not None
        assert tmpl.risk_level == risk, (
            f"{tool_name}: expected risk_level {risk!r}, got {tmpl.risk_level!r}"
        )


def test_template_required_permissions():
    """Verify required_permission matches tool definitions."""
    from cauldron_ai.prompt_templates import get_prompt_template_registry
    registry = get_prompt_template_registry()

    _PERM_VIEW = "cauldron_content_operations.view_published_content"
    _PERM_PROPOSE = "cauldron_content_operations.propose_content_changes"
    _PERM_MAINTAIN = "cauldron_content_operations.apply_content_changes"

    expected_perms = {
        "site.inspect": _PERM_VIEW,
        "site.stage_theme": _PERM_PROPOSE,
        "site.prepare_change_set": _PERM_PROPOSE,
        "site.inspect_preview": _PERM_VIEW,
        "site.publish": _PERM_MAINTAIN,
    }
    for tool_name, perm in expected_perms.items():
        tmpl = registry.get_tool_template(tool_name)
        assert tmpl is not None
        assert tmpl.required_permission == perm, (
            f"{tool_name}: expected required_permission {perm!r}, "
            f"got {tmpl.required_permission!r}"
        )


def test_registration_is_idempotent():
    """Calling register_builtin_site_tool_prompts() twice is a no-op."""
    from cauldron_ai.prompt_templates import get_prompt_template_registry
    from cauldron_site_astro.site_tool_prompts import register_builtin_site_tool_prompts

    registry = get_prompt_template_registry()
    before = {t.tool_name: t for t in registry.all_tool_templates()}

    # Second call must not raise and must not change the registered instances
    register_builtin_site_tool_prompts()

    after = {t.tool_name: t for t in registry.all_tool_templates()}
    for tool_name in EXPECTED_SITE_TOOLS:
        assert before.get(tool_name) is after.get(tool_name), (
            f"Re-registration replaced the template instance for {tool_name!r}"
        )


def test_e017_no_missing_templates_for_site_tools():
    """E017 logic: every site tool must have a prompt template registered."""
    from cauldron_ai_admin.tools import get_tool_registry
    from cauldron_ai.prompt_templates import get_prompt_template_registry

    tool_registry = get_tool_registry()
    template_registry = get_prompt_template_registry()

    registered_tools = {d.name for d in tool_registry.all_definitions()}
    missing = sorted(
        name for name in EXPECTED_SITE_TOOLS
        if name in registered_tools and template_registry.get_tool_template(name) is None
    )
    assert missing == [], (
        f"E017 would fire: site tools missing prompt templates: {missing!r}"
    )


def test_e021_permission_alignment_for_site_tools():
    """E021 logic: template required_permission must match tool definition for site tools."""
    from cauldron_ai_admin.tools import get_tool_registry
    from cauldron_ai.prompt_templates import get_prompt_template_registry

    tool_registry = get_tool_registry()
    template_registry = get_prompt_template_registry()

    mismatches = []
    for defn in tool_registry.all_definitions():
        if defn.name not in EXPECTED_SITE_TOOLS:
            continue
        tmpl = template_registry.get_tool_template(defn.name)
        if tmpl is None:
            continue
        tool_perm = getattr(defn, "required_permission", None)
        if tmpl.required_permission != tool_perm:
            mismatches.append(
                f"{defn.name}: template has {tmpl.required_permission!r}, "
                f"tool has {tool_perm!r}"
            )

    assert mismatches == [], (
        "E021 would fire — permission mismatch:\n" + "\n".join(mismatches)
    )
