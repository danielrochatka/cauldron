"""Future-module acceptance test.

Proves that a module completely unknown to the tree implementation
automatically appears in the graph when registered in the module registry.
No slug needs to be hardcoded in cauldron_module_tree.
"""
import json
import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _make_registry(inventory_entries, *, capabilities=None, errors=()):
    reg = MagicMock()
    reg.inventory.return_value = list(inventory_entries)
    reg.capabilities.return_value = capabilities or {}
    reg.errors.return_value = list(errors)
    reg.graph_info.return_value = list(inventory_entries)
    return reg


def _entry(slug, **kwargs):
    from cauldron.modules import ModuleManifest
    manifest = ModuleManifest(slug=slug, label=slug.replace(".", " ").title())
    defaults = {
        "slug": slug,
        "label": manifest.label,
        "version": "1.0.0",
        "state": "ready",
        "enabled": True,
        "active": True,
        "load_index": 0,
        "source_type": "package",
        "source": "test-pkg",
        "manifest": manifest.to_dict(),
        "provides": [],
        "requires": [],
        "optional": [],
        "deps": [],
        "django_apps": [],
        "errors": [],
        "requires_restart": False,
        "cauldron_version_ok": True,
        "installed_cauldron_version": "0.1.0",
    }
    defaults.update(kwargs)
    return defaults


@contextmanager
def _fake_registry_ctx(inventory_entries, *, capabilities=None, errors=()):
    fake_registry = MagicMock()
    fake_registry.inventory.return_value = list(inventory_entries)
    fake_registry.capabilities.return_value = capabilities or {}
    fake_registry.errors.return_value = list(errors)
    fake_registry.graph_info.return_value = list(inventory_entries)

    fake_module = types.ModuleType("cauldron.modules.registry")
    fake_module.registry = fake_registry

    original = sys.modules.get("cauldron.modules.registry")
    sys.modules["cauldron.modules.registry"] = fake_module
    try:
        yield fake_registry
    finally:
        if original is None:
            sys.modules.pop("cauldron.modules.registry", None)
        else:
            sys.modules["cauldron.modules.registry"] = original


# --------------------------------------------------------------------------- #
# Future-module tests                                                           #
# --------------------------------------------------------------------------- #

def test_unknown_future_module_appears_in_graph_nodes():
    """A module completely unknown to the tree appears in nodes when added to registry."""
    from cauldron_module_tree.graph import build_graph

    future_slug = "future.unknown.module"
    e = _entry(future_slug)
    registry = _make_registry([e])
    result = build_graph(registry)
    slugs = [n["slug"] for n in result["nodes"]]
    assert future_slug in slugs, f"Expected {future_slug!r} in nodes; got {slugs}"


def test_future_module_with_no_presentation_gets_fallback_icon():
    """Future module with empty icon_svg gets a non-empty fallback SVG."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest, ModulePresentation

    slug = "future.unknown.module"
    manifest = ModuleManifest(
        slug=slug,
        label="Future Unknown Module",
        presentation=ModulePresentation(icon_svg=""),
    )
    e = _entry(slug, manifest=manifest.to_dict())
    registry = _make_registry([e])
    result = build_graph(registry)
    node = next(n for n in result["nodes"] if n["slug"] == slug)
    assert node["icon_svg"] != "", "Expected non-empty fallback icon_svg"
    assert "<svg" in node["icon_svg"]


def test_future_module_parents_derived_correctly():
    """Future module that is depended on by another module shows correct parents."""
    from cauldron_module_tree.graph import build_graph

    future_slug = "future.core.provider"
    consumer_slug = "existing.consumer"

    future = _entry(future_slug)
    consumer = _entry(consumer_slug, requires=[{"slug": future_slug, "kind": "module"}])
    registry = _make_registry([consumer, future])
    result = build_graph(registry)

    node_by_slug = {n["slug"]: n for n in result["nodes"]}
    assert future_slug in node_by_slug
    parents = node_by_slug[future_slug]["parents"]
    assert consumer_slug in parents, f"Expected {consumer_slug!r} in parents of {future_slug!r}; got {parents}"


def test_future_module_edges_created():
    """Future module with requires creates correct edges."""
    from cauldron_module_tree.graph import build_graph

    future_slug = "future.dependent.mod"
    dep_slug = "existing.dep"

    future = _entry(future_slug, requires=[{"slug": dep_slug, "kind": "module"}])
    dep = _entry(dep_slug)
    registry = _make_registry([future, dep])
    result = build_graph(registry)

    edges = result["edges"]
    matching = [
        e for e in edges
        if e["source"] == future_slug and e["target"] == dep_slug and e["kind"] == "required"
    ]
    assert matching, (
        f"Expected a required edge from {future_slug!r} to {dep_slug!r}; "
        f"got edges: {edges}"
    )


@pytest.mark.django_db
def test_future_module_appears_in_graph_api():
    """HTTP-level test: fake registry with future module slug — graph API returns it in response nodes."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client
    from django.urls import reverse

    User = get_user_model()
    user = User.objects.create_user(username="future_api_user", password="testpass123")
    try:
        perm = Permission.objects.get(codename="view_module_tree")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    future_slug = "future.api.module"
    entry = _entry(future_slug)

    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        url = reverse("cauldron_module_tree:graph_api")
        response = client.get(url)

    assert response.status_code == 200
    data = json.loads(response.content)
    assert "nodes" in data
    slugs = [n["slug"] for n in data["nodes"]]
    assert future_slug in slugs, f"Expected {future_slug!r} in API response nodes; got {slugs}"
