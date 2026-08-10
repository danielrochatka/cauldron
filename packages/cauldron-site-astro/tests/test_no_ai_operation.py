"""Tests verifying Site Astro operates correctly without AI packages installed."""
import importlib
import sys
from unittest.mock import MagicMock, patch


def _remove_module(name: str) -> None:
    """Remove a module and all its sub-modules from sys.modules."""
    to_remove = [k for k in list(sys.modules) if k == name or k.startswith(name + ".")]
    for k in to_remove:
        del sys.modules[k]


def test_register_site_tools_safe_without_ai_admin(monkeypatch):
    """_register_site_tools must not raise when cauldron_ai_admin is absent."""
    # Simulate cauldron_ai_admin not being installed
    monkeypatch.setitem(sys.modules, "cauldron_ai_admin", None)
    monkeypatch.setitem(sys.modules, "cauldron_ai_admin.tools", None)

    from cauldron_site_astro.apps import _register_site_tools
    # Should complete without raising
    _register_site_tools()


def test_register_site_tool_prompts_safe_without_cauldron_ai(monkeypatch):
    """_register_site_tool_prompts must not raise when cauldron_ai is absent."""
    monkeypatch.setitem(sys.modules, "cauldron_ai", None)

    from cauldron_site_astro.apps import _register_site_tool_prompts
    _register_site_tool_prompts()


def test_connect_signals_safe_without_content_operations(monkeypatch):
    """_connect_signals must not raise when cauldron_content_operations is absent."""
    monkeypatch.setitem(sys.modules, "cauldron_content_operations", None)
    monkeypatch.setitem(sys.modules, "cauldron_content_operations.signals", None)

    from cauldron_site_astro.apps import _connect_signals
    _connect_signals()


def test_site_astro_manifest_ai_deps_are_optional():
    """Manifest must not hard-require AI modules, and must not reference admin.content.

    Dependency direction:
      site.astro → (optional) cauldron.ai.admin   (tool registration, prompt templates)
      site.astro → (optional) cauldron.ai          (prompt templates only)
      cauldron.admin.content uses importlib for site.astro — NO manifest edge back.

    cauldron.admin.content must never appear in site.astro's requires or optional;
    that edge would complete the cycle admin.content → site.astro → ai.admin → admin.content.
    cauldron.ai.admin is correctly optional (site_tools.py imports AdminAIToolDefinition etc.).
    """
    from cauldron_site_astro.module import module
    manifest = module.manifest

    required_slugs = {r.slug for r in manifest.requires}
    optional_slugs = {r.slug for r in manifest.optional}

    # admin.content must NOT appear — it would create a 3-node cycle.
    assert "cauldron.admin.content" not in required_slugs, (
        "cauldron.admin.content must not be in requires — that creates a circular dependency"
    )
    assert "cauldron.admin.content" not in optional_slugs, (
        "cauldron.admin.content must not be in optional — that creates a circular dependency; "
        "admin.content accesses site.astro via importlib with no manifest edge"
    )

    # AI deps must be optional, not required.
    assert "cauldron.ai.admin" not in required_slugs, (
        "cauldron.ai.admin must not be hard-required by site.astro"
    )
    assert "cauldron.ai.admin" in optional_slugs, (
        "cauldron.ai.admin must be optional — site_tools.py uses AdminAIToolDefinition etc."
    )
    assert "cauldron.ai" not in required_slugs
    assert "cauldron.ai" in optional_slugs


def test_site_astro_manifest_core_requires():
    """Site Astro's core required path must include content.operations."""
    from cauldron_site_astro.module import module
    manifest = module.manifest

    required_slugs = {r.slug for r in manifest.requires}
    assert "cauldron.content.operations" in required_slugs, (
        "cauldron.content.operations is Site Astro's core required module"
    )
