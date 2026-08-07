"""Tests for admin_ai.E022/E023 — manifest declarations vs runtime registries."""
import pytest
from unittest.mock import patch

from cauldron.modules import BaseModule, ModuleManifest
from cauldron_ai_admin.checks import check_manifest_ai_tools_registered


def _make_module(slug, *, ai_tools=(), prompt_templates=()):
    return BaseModule(ModuleManifest(
        slug=slug,
        label=slug,
        ai_tools=tuple(ai_tools),
        prompt_templates=tuple(prompt_templates),
    ))


@pytest.fixture(autouse=True)
def reset_module_registry():
    """Snapshot and restore the global module registry around each test."""
    from cauldron.modules.registry import registry

    snap = {
        "_discovered": dict(registry._discovered),
        "_active": dict(registry._active),
        "_load_order": list(registry._load_order),
        "_capability_providers": dict(registry._capability_providers),
        "_capability_overrides": dict(registry._capability_overrides),
        "_module_configs": dict(registry._module_configs),
        "_errors": list(registry._errors),
        "_warnings": list(registry._warnings),
        "_discovery_errors": list(registry._discovery_errors),
        "_lifecycle_errors": list(registry._lifecycle_errors),
        "_enabled": set(registry._enabled),
        "_discovery_records": list(registry._discovery_records),
        "_unavailable": list(registry._unavailable),
        "_populated": registry._populated,
        "_ready": registry._ready,
    }
    yield
    for attr, value in snap.items():
        setattr(registry, attr, value)


def _inject_and_activate(modules):
    from cauldron.modules.registry import registry
    registry.populate(modules)
    registry.activate()


def _patch_active():
    return patch("cauldron_ai_admin.checks._is_admin_ai_active", return_value=True)


def _patch_tool_registry(names=()):
    import types
    defs = [types.SimpleNamespace(name=n) for n in names]
    mock_reg = types.SimpleNamespace(all_definitions=lambda: defs)
    return patch("cauldron_ai_admin.checks.check_manifest_ai_tools_registered.__wrapped__"
                 if hasattr(check_manifest_ai_tools_registered, "__wrapped__") else
                 "cauldron_ai_admin.tools.get_tool_registry", return_value=mock_reg)


class TestManifestIntegrityCheck:

    def test_no_e022_when_no_ai_tools_declared(self):
        m = _make_module("mod.a")  # no ai_tools
        _inject_and_activate([m])
        with _patch_active():
            with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
                 patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
                import types
                mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
                mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
                messages = check_manifest_ai_tools_registered(None)
        assert messages == []

    def test_e022_emitted_for_missing_ai_tool(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert len(e022) == 1
        assert "mymod.my_tool" in e022[0].msg

    def test_no_e022_when_declared_tool_registered(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            tool_def = types.SimpleNamespace(name="mymod.my_tool")
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [tool_def])
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert e022 == []

    def test_e023_emitted_for_missing_prompt_template(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
            messages = check_manifest_ai_tools_registered(None)
        e023 = [m for m in messages if m.id == "admin_ai.E023"]
        assert len(e023) == 1
        assert "mymod.my_tool" in e023[0].msg

    def test_no_e023_when_declared_template_registered(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
            tmpl = types.SimpleNamespace(tool_name="mymod.my_tool")
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [tmpl])
            messages = check_manifest_ai_tools_registered(None)
        e023 = [m for m in messages if m.id == "admin_ai.E023"]
        assert e023 == []

    def test_e022_obj_is_module_slug(self):
        m = _make_module("owner.module", ai_tools=("owner.module.tool",))
        _inject_and_activate([m])
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert e022 and e022[0].obj == "owner.module"

    def test_returns_empty_when_registry_not_ready(self):
        from cauldron.modules.registry import registry
        m = _make_module("a", ai_tools=("a.tool",))
        registry.populate([m])
        # Do NOT call activate(); is_ready is False.
        with patch("cauldron_ai_admin.tools.get_tool_registry") as mock_tools, \
             patch("cauldron_ai.prompt_templates.get_prompt_template_registry") as mock_prompts:
            import types
            mock_tools.return_value = types.SimpleNamespace(all_definitions=lambda: [])
            mock_prompts.return_value = types.SimpleNamespace(all_tool_templates=lambda: [])
            messages = check_manifest_ai_tools_registered(None)
        assert messages == []

    def test_returns_empty_on_import_error(self, monkeypatch):
        """If get_tool_registry can't be imported, check returns [] gracefully."""
        import sys
        monkeypatch.setitem(sys.modules, "cauldron_ai_admin.tools", None)
        messages = check_manifest_ai_tools_registered(None)
        assert messages == []
