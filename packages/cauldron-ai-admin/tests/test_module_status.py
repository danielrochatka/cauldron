"""ITEM 3 — system.module_status capability + dependency reporting.

The module-level capability map is sourced strictly from
``module_registry.capabilities()``. The AI provider registry is a separate
per-request registry and never contributes to this diagnostic view.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.builtin_tools import _handle_module_status
from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult


def _ctx():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="module-status-user")
    return AdminAIToolContext(actor=user, run_id="r", correlation_id="c")


def _fake_registry(
    *,
    capabilities: dict | None = None,
    graph: list | None = None,
    dependency_graph: dict | None = None,
    lifecycle_errors: list | None = None,
    discovery_errors: list | None = None,
    errors: list | None = None,
):
    """Build a MagicMock module_registry with typed return values."""
    reg = MagicMock()
    reg.capabilities.return_value = capabilities or {}
    reg.graph_info.return_value = graph or []
    reg.dependency_graph.return_value = dependency_graph or {}
    reg.lifecycle_errors.return_value = lifecycle_errors or []
    reg.discovery_errors.return_value = discovery_errors or []
    reg.errors.return_value = errors or []
    return reg


def test_sole_capability_provider_selected():
    """Exactly one provider → selected == that provider, not ambiguous."""
    reg = _fake_registry(
        capabilities={"ai.model.providers": ["cauldron.ai"]},
        graph=[{
            "slug": "cauldron.ai",
            "version": "0.1.0",
            "active": True,
            "provides": ["ai.model.providers"],
            "requires": [],
            "deps": [],
        }],
        dependency_graph={"cauldron.ai": []},
    )
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    assert isinstance(result, AdminAIToolResult)
    cap = result.data["capabilities"]["ai.model.providers"]
    assert cap["providers"] == ["cauldron.ai"]
    assert cap["selected"] == "cauldron.ai"
    assert cap["ambiguous"] is False


def test_explicit_override_selected():
    """Multiple providers + valid override → selected = override."""
    reg = _fake_registry(
        capabilities={"ai.model.providers": ["cauldron.ai", "cauldron.ai.other"]},
        graph=[
            {
                "slug": "cauldron.ai",
                "version": "0.1.0",
                "active": True,
                "provides": ["ai.model.providers"],
                "requires": [],
                "deps": [],
            },
            {
                "slug": "cauldron.ai.other",
                "version": "0.1.0",
                "active": True,
                "provides": ["ai.model.providers"],
                "requires": [],
                "deps": [],
            },
        ],
    )
    with patch("cauldron.modules.registry.registry", reg), \
         override_settings(CAULDRON_CAPABILITY_PROVIDERS={
             "ai.model.providers": "cauldron.ai",
         }):
        result = _handle_module_status(_ctx())
    cap = result.data["capabilities"]["ai.model.providers"]
    assert set(cap["providers"]) == {"cauldron.ai", "cauldron.ai.other"}
    assert cap["selected"] == "cauldron.ai"
    assert cap["ambiguous"] is False


def test_ambiguous_no_override():
    """Multiple providers, no override → selected None, ambiguous True."""
    reg = _fake_registry(
        capabilities={"ai.model.providers": ["cauldron.ai", "cauldron.ai.other"]},
    )
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    cap = result.data["capabilities"]["ai.model.providers"]
    assert set(cap["providers"]) == {"cauldron.ai", "cauldron.ai.other"}
    assert cap["selected"] is None
    assert cap["ambiguous"] is True


def test_invalid_override_treated_as_ambiguous():
    """Override that names a module NOT in providers list → ambiguous."""
    reg = _fake_registry(
        capabilities={"ai.model.providers": ["cauldron.ai", "cauldron.ai.other"]},
    )
    with patch("cauldron.modules.registry.registry", reg), \
         override_settings(CAULDRON_CAPABILITY_PROVIDERS={
             "ai.model.providers": "nonexistent.module",
         }):
        result = _handle_module_status(_ctx())
    cap = result.data["capabilities"]["ai.model.providers"]
    assert cap["selected"] is None
    assert cap["ambiguous"] is True


def test_resolved_dep_in_registry_is_ok():
    """Resolved dep that appears in registry (active or discovered) → ok."""
    reg = _fake_registry(
        graph=[
            {
                "slug": "cauldron.ai",
                "version": "0.1.0",
                "active": True,
                "provides": [],
                "requires": [],
                "deps": ["cauldron.django.state"],
            },
            {
                "slug": "cauldron.django.state",
                "version": "0.1.0",
                "active": True,
                "provides": [],
                "requires": [],
                "deps": [],
            },
        ],
        dependency_graph={
            "cauldron.ai": ["cauldron.django.state"],
            "cauldron.django.state": [],
        },
    )
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    entry = next(m for m in result.data["modules"] if m["name"] == "cauldron.ai")
    assert entry["dependencies"] == ["cauldron.django.state"]
    assert entry["dependency_health"] == {"cauldron.django.state": "ok"}


def test_missing_dep_is_missing():
    """Resolved dep that is NOT registered → missing."""
    reg = _fake_registry(
        graph=[{
            "slug": "cauldron.ai",
            "version": "0.1.0",
            "active": True,
            "provides": [],
            "requires": [],
            "deps": ["cauldron.missing"],
        }],
        dependency_graph={"cauldron.ai": ["cauldron.missing"]},
    )
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    entry = result.data["modules"][0]
    assert entry["dependencies"] == ["cauldron.missing"]
    assert entry["dependency_health"] == {"cauldron.missing": "missing"}


def test_discovery_error_sanitized():
    """Discovery error strings must never contain filesystem paths."""
    bad = MagicMock()
    bad.message = "/home/user/secret/path: could not import module"
    reg = _fake_registry(discovery_errors=[bad])
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    errs = result.data["discovery_errors"]
    joined = " ".join(errs)
    assert "/home/user/secret/path" not in joined
    assert "could not import module" not in joined  # after-colon stripped
    # Should keep just the leading label ("" here, since path was pre-colon).
    # The important guarantee is no secrets leak.


def test_lifecycle_error_produces_degraded():
    """A module with a lifecycle error has status='error' and health='degraded'."""
    err = MagicMock()
    err.module_slug = "cauldron.broken"
    err.phase = "register"
    reg = _fake_registry(
        graph=[{
            "slug": "cauldron.broken",
            "version": "1.0.0",
            "active": True,
            "provides": [],
            "requires": [],
            "deps": [],
        }],
        dependency_graph={"cauldron.broken": []},
        lifecycle_errors=[err],
    )
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    entry = result.data["modules"][0]
    assert entry["status"] == "error"
    assert entry["health"] == "degraded"


def test_resolution_error_sanitized():
    """ResolutionError messages are truncated at ':' to avoid detail leaks."""
    err = MagicMock()
    err.message = "cauldron.foo: dependency cauldron.bar not found"
    reg = _fake_registry(errors=[err])
    with patch("cauldron.modules.registry.registry", reg):
        result = _handle_module_status(_ctx())
    errs = result.data["resolution_errors"]
    # Only the module-slug label ("cauldron.foo") should be surfaced.
    assert errs == ["cauldron.foo"]
