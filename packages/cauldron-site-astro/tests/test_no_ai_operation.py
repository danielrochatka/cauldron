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
    """Manifest must not hard-require AI or consumer modules.

    cauldron.ai.admin and cauldron.admin.content are *consumers* of site.astro's
    publish service; they declare the optional dep on site.astro (not vice versa)
    to avoid circular dependency in the module graph.  cauldron.ai remains an
    optional dep of site.astro (prompt template registration).
    """
    from cauldron_site_astro.module import module
    manifest = module.manifest

    required_slugs = {r.slug for r in manifest.requires}
    optional_slugs = {r.slug for r in manifest.optional}

    consumer_slugs = {"cauldron.ai.admin", "cauldron.admin.content"}
    for slug in consumer_slugs:
        assert slug not in required_slugs, (
            f"{slug} must not be in requires — site.astro is its provider, not consumer"
        )
        assert slug not in optional_slugs, (
            f"{slug} must not be in optional — that would create a circular module dependency; "
            f"the dep flows the other way: {slug} → cauldron.site.astro"
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
