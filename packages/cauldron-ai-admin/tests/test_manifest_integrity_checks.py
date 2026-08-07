"""Tests for admin_ai.E022-E025 — manifest declarations vs runtime registries."""
import types
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


def _make_tool_def(name, owning_module):
    return types.SimpleNamespace(name=name, owning_module=owning_module)


def _make_tmpl(tool_name, owning_module):
    return types.SimpleNamespace(tool_name=tool_name, owning_module=owning_module)


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
        "_module_states": dict(registry._module_states),
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


def _patch_registries(tool_defs=(), tmpl_defs=()):
    mock_tools = types.SimpleNamespace(all_definitions=lambda: list(tool_defs))
    mock_prompts = types.SimpleNamespace(all_tool_templates=lambda: list(tmpl_defs))
    return (
        patch("cauldron_ai_admin.tools.get_tool_registry", return_value=mock_tools),
        patch("cauldron_ai.prompt_templates.get_prompt_template_registry", return_value=mock_prompts),
    )


class TestManifestIntegrityCheck:

    def test_no_errors_when_no_ai_tools_declared(self):
        m = _make_module("mod.a")
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries()
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        assert messages == []

    # --- E022: missing tool ---

    def test_e022_emitted_for_missing_ai_tool(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries()
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert len(e022) == 1
        assert "mymod.my_tool" in e022[0].msg

    def test_no_e022_when_declared_tool_registered_with_correct_owner(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tool_defs=[_make_tool_def("mymod.my_tool", "mymod")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert e022 == []

    # --- E024: wrong tool owner ---

    def test_e024_emitted_when_tool_owned_by_different_module(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tool_defs=[_make_tool_def("mymod.my_tool", "some.other.module")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e024 = [m for m in messages if m.id == "admin_ai.E024"]
        assert len(e024) == 1
        assert "mymod" in e024[0].msg
        assert "some.other.module" in e024[0].msg

    def test_e024_no_e022_when_tool_exists_wrong_owner(self):
        m = _make_module("mymod", ai_tools=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tool_defs=[_make_tool_def("mymod.my_tool", "other.module")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        assert not any(m.id == "admin_ai.E022" for m in messages)
        assert any(m.id == "admin_ai.E024" for m in messages)

    # --- E023: missing template ---

    def test_e023_emitted_for_missing_prompt_template(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries()
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e023 = [m for m in messages if m.id == "admin_ai.E023"]
        assert len(e023) == 1
        assert "mymod.my_tool" in e023[0].msg

    def test_no_e023_when_declared_template_registered_with_correct_owner(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tmpl_defs=[_make_tmpl("mymod.my_tool", "mymod")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e023 = [m for m in messages if m.id == "admin_ai.E023"]
        assert e023 == []

    # --- E025: wrong template owner ---

    def test_e025_emitted_when_template_owned_by_different_module(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tmpl_defs=[_make_tmpl("mymod.my_tool", "some.other.module")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e025 = [m for m in messages if m.id == "admin_ai.E025"]
        assert len(e025) == 1
        assert "mymod" in e025[0].msg
        assert "some.other.module" in e025[0].msg

    def test_e025_no_e023_when_template_exists_wrong_owner(self):
        m = _make_module("mymod", prompt_templates=("mymod.my_tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries(
            tmpl_defs=[_make_tmpl("mymod.my_tool", "other.module")],
        )
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        assert not any(m.id == "admin_ai.E023" for m in messages)
        assert any(m.id == "admin_ai.E025" for m in messages)

    # --- metadata ---

    def test_e022_obj_is_module_slug(self):
        m = _make_module("owner.module", ai_tools=("owner.module.tool",))
        _inject_and_activate([m])
        p_tools, p_prompts = _patch_registries()
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        e022 = [m for m in messages if m.id == "admin_ai.E022"]
        assert e022 and e022[0].obj == "owner.module"

    def test_returns_empty_when_registry_not_ready(self):
        from cauldron.modules.registry import registry
        m = _make_module("a", ai_tools=("a.tool",))
        registry.populate([m])
        # Do NOT call activate(); is_ready is False.
        p_tools, p_prompts = _patch_registries()
        with p_tools, p_prompts:
            messages = check_manifest_ai_tools_registered(None)
        assert messages == []

    def test_returns_empty_on_import_error(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "cauldron_ai_admin.tools", None)
        messages = check_manifest_ai_tools_registered(None)
        assert messages == []
