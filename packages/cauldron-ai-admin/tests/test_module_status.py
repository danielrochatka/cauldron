"""ITEM 5 — system.module_status capability + dependency reporting."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.builtin_tools import _handle_module_status
from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult
from cauldron_ai.providers import _reset_registry_for_tests, register_provider


class _P:
    def __init__(self, name):
        self.name = name

    def complete(self, req):  # pragma: no cover
        return None


@pytest.fixture(autouse=True)
def reset_ai_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _ctx():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="module-status-user")
    return AdminAIToolContext(actor=user, run_id="r", correlation_id="c")


def _fake_registry(provides=("ai.model.providers",), requires=(), deps=()):
    reg = MagicMock()
    reg.graph_info.return_value = [{
        "slug": "cauldron.ai",
        "provides": list(provides),
        "requires": [{"slug": r, "kind": "capability", "version": ""} for r in requires],
        "deps": list(deps),
        "active": True,
        "version": "0.1.0",
    }]
    reg.lifecycle_errors.return_value = []
    reg.capabilities.return_value = {}
    return reg


def test_module_status_single_ai_provider_selected():
    register_provider(_P("fake"))
    fake_registry = _fake_registry(provides=("ai.model.providers",))
    with patch("cauldron.modules.registry.registry", fake_registry):
        result = _handle_module_status(_ctx())
    assert isinstance(result, AdminAIToolResult)
    entry = result.data["modules"][0]
    caps = entry["capability_providers"]["ai.model.providers"]
    assert caps["providers"] == ["fake"]
    assert caps["selected"] == "fake"
    assert caps["ambiguous"] is False
    assert caps["resolution_errors"] == []


def test_module_status_two_providers_with_valid_override():
    register_provider(_P("fake"))
    register_provider(_P("anthropic"))
    fake_registry = _fake_registry(provides=("ai.model.providers",))
    with patch("cauldron.modules.registry.registry", fake_registry), \
         override_settings(CAULDRON_CAPABILITY_PROVIDERS={
             "ai.model.providers": "anthropic",
         }):
        result = _handle_module_status(_ctx())
    entry = result.data["modules"][0]
    caps = entry["capability_providers"]["ai.model.providers"]
    assert set(caps["providers"]) == {"fake", "anthropic"}
    assert caps["selected"] == "anthropic"
    assert caps["ambiguous"] is False
    assert caps["resolution_errors"] == []


def test_module_status_two_providers_without_override_is_ambiguous():
    register_provider(_P("fake"))
    register_provider(_P("anthropic"))
    fake_registry = _fake_registry(provides=("ai.model.providers",))
    with patch("cauldron.modules.registry.registry", fake_registry):
        result = _handle_module_status(_ctx())
    entry = result.data["modules"][0]
    caps = entry["capability_providers"]["ai.model.providers"]
    assert set(caps["providers"]) == {"fake", "anthropic"}
    assert caps["selected"] is None
    assert caps["ambiguous"] is True
    assert caps["resolution_errors"], "expected an ambiguity error string"


def test_module_status_dependency_health_ok_when_in_installed_apps():
    ctx = _ctx()
    reg = _fake_registry(
        provides=(),
        requires=("cauldron.state",),
    )
    # Use a dependency slug that matches an installed app label. The
    # default test project already installs cauldron_content_operations
    # so we assert against a known-present entry.
    reg.graph_info.return_value = [{
        "slug": "cauldron.ai",
        "provides": [],
        "requires": [
            {"slug": "cauldron_content_operations", "kind": "module", "version": ""},
        ],
        "deps": [],
        "active": True,
        "version": "0.1.0",
    }]
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(ctx)
    entry = result.data["modules"][0]
    assert entry["dependency_health"] == {"cauldron_content_operations": "ok"}


def test_module_status_dependency_health_missing_when_absent():
    ctx = _ctx()
    reg = _fake_registry()
    reg.graph_info.return_value = [{
        "slug": "cauldron.ai",
        "provides": [],
        "requires": [
            {"slug": "no.such.absent.app", "kind": "module", "version": ""},
        ],
        "deps": [],
        "active": True,
        "version": "0.1.0",
    }]
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(ctx)
    entry = result.data["modules"][0]
    assert entry["dependency_health"] == {"no.such.absent.app": "missing"}
