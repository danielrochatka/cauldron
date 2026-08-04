"""Tests for the module inventory API view."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cauldron.modules import BaseModule, ModuleManifest
from cauldron.modules.registry import ModuleRegistry


def _mod(slug, *, version="1.0.0", provides=()):
    return BaseModule(ModuleManifest(slug=slug, label=slug, version=version, provides=provides))


def _make_response(test_registry):
    """Call the view with a minimal fake request and return the parsed JSON."""
    from django.test import RequestFactory
    from cauldron.views import module_inventory
    import cauldron.modules.registry as reg_module

    rf = RequestFactory()
    request = rf.get("/api/cauldron/modules/")
    with patch.object(reg_module, "registry", test_registry):
        response = module_inventory(request)
    return json.loads(response.content)


class TestModuleInventoryView:
    def test_empty_registry_returns_empty_list(self):
        reg = ModuleRegistry()
        reg.populate([])
        data = _make_response(reg)
        assert data == {"modules": []}

    def test_discovered_module_appears_in_response(self):
        reg = ModuleRegistry()
        a = _mod("a")
        reg.populate([a])
        reg.activate()
        data = _make_response(reg)
        modules = data["modules"]
        assert len(modules) == 1
        m = modules[0]
        assert m["slug"] == "a"
        assert m["label"] == "a"
        assert m["version"] == "1.0.0"
        assert m["state"] == "ready"
        assert m["enabled"] is True

    def test_disabled_module_has_disabled_state(self):
        reg = ModuleRegistry()
        a = _mod("a")
        b = _mod("b")
        reg.populate([a, b], enabled={"a"})
        reg.activate()
        data = _make_response(reg)
        by_slug = {m["slug"]: m for m in data["modules"]}
        assert by_slug["b"]["enabled"] is False
        assert by_slug["b"]["state"] == "disabled"
        assert by_slug["a"]["state"] == "ready"

    def test_failed_module_state_in_response(self):
        class Broken(BaseModule):
            def on_ready(self):
                raise RuntimeError("boom")

        reg = ModuleRegistry()
        a = Broken(ModuleManifest(slug="a", label="a"))
        reg.populate([a])
        reg.activate()
        data = _make_response(reg)
        m = data["modules"][0]
        assert m["state"] == "failed"
        # Errors present
        assert len(m["errors"]) == 1
        err = m["errors"][0]
        assert err["phase"] == "on_ready"
        assert "exception_type" in err
        # Raw exception message NOT exposed
        assert "boom" not in json.dumps(data)

    def test_no_absolute_paths_in_response(self, tmp_path):
        reg = ModuleRegistry()
        a = _mod("a")
        reg.populate([a])
        reg.activate()
        data = _make_response(reg)
        raw = json.dumps(data)
        assert str(tmp_path) not in raw
        assert "/home" not in raw

    def test_provides_and_requires_serialized(self):
        from cauldron.modules import ModuleRequirement
        reg = ModuleRegistry()
        a = BaseModule(ModuleManifest(
            slug="a",
            label="A",
            provides=("cap.x",),
        ))
        b = BaseModule(ModuleManifest(
            slug="b",
            label="B",
            requires=(ModuleRequirement(slug="a"),),
        ))
        reg.populate([a, b])
        reg.activate()
        data = _make_response(reg)
        by_slug = {m["slug"]: m for m in data["modules"]}
        assert "cap.x" in by_slug["a"]["provides"]
        assert "a" in by_slug["b"]["requires"]
