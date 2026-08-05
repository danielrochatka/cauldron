"""Tests for the graph builder."""
from unittest.mock import MagicMock


def _make_registry(inventory_entries, *, capabilities=None, errors=()):
    reg = MagicMock()
    reg.inventory.return_value = list(inventory_entries)
    reg.capabilities.return_value = capabilities or {}
    reg.errors.return_value = list(errors)
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


def test_empty_registry_returns_empty_graph():
    """Empty inventory returns empty nodes and edges."""
    from cauldron_module_tree.graph import build_graph
    registry = _make_registry([])
    result = build_graph(registry).to_api_dict()
    assert result["nodes"] == []
    assert result["edges"] == []
    assert "metadata" in result


def test_single_node_has_correct_fields():
    """Node has slug, title, state, etc."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module")
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["slug"] == "my.module"
    assert "title" in node
    assert "state" in node
    assert "enabled" in node
    assert "active" in node
    assert "version" in node
    assert "icon_svg" in node
    assert "parents" in node
    assert "children" in node


def test_node_title_falls_back_to_label():
    """No presentation title — uses manifest label."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest, ModulePresentation
    manifest = ModuleManifest(
        slug="my.module",
        label="My Module",
        presentation=ModulePresentation(title=""),
    )
    e = _entry("my.module", manifest=manifest.to_dict())
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert node["title"] == "My Module"


def test_required_edge_created():
    """Entry with requires=[{slug: 'b', kind: 'module'}] creates edge."""
    from cauldron_module_tree.graph import build_graph
    a = _entry("mod.a", requires=[{"slug": "mod.b", "kind": "module"}])
    b = _entry("mod.b")
    registry = _make_registry([a, b])
    result = build_graph(registry).to_api_dict()
    edges = result["edges"]
    assert any(
        e["source"] == "mod.a" and e["target"] == "mod.b" and e["kind"] == "required"
        for e in edges
    )


def test_optional_edge_created():
    """Entry with optional=[...] creates edge with kind='optional'."""
    from cauldron_module_tree.graph import build_graph
    a = _entry("mod.a", optional=[{"slug": "mod.b", "kind": "module"}])
    b = _entry("mod.b")
    registry = _make_registry([a, b])
    result = build_graph(registry).to_api_dict()
    edges = result["edges"]
    assert any(
        e["source"] == "mod.a" and e["target"] == "mod.b" and e["kind"] == "optional"
        for e in edges
    )


def test_capability_edge_created():
    """Entry with requires=[{slug: 'cap', kind: 'capability'}] and capabilities map creates capability edge."""
    from cauldron_module_tree.graph import build_graph
    consumer = _entry("mod.consumer", requires=[{"slug": "my.cap", "kind": "capability"}])
    provider = _entry("mod.provider", provides=["my.cap"])
    registry = _make_registry(
        [consumer, provider],
        capabilities={"my.cap": ["mod.provider"]},
    )
    result = build_graph(registry).to_api_dict()
    edges = result["edges"]
    assert any(
        e["source"] == "mod.consumer"
        and e["target"] == "mod.provider"
        and e["kind"] == "capability"
        and e["capability"] == "my.cap"
        for e in edges
    )


def test_parent_derived_from_child_edges():
    """If A depends on B, B's parents include A."""
    from cauldron_module_tree.graph import build_graph
    a = _entry("mod.a", requires=[{"slug": "mod.b", "kind": "module"}])
    b = _entry("mod.b")
    registry = _make_registry([a, b])
    result = build_graph(registry).to_api_dict()
    node_by_slug = {n["slug"]: n for n in result["nodes"]}
    assert "mod.a" in node_by_slug["mod.b"]["parents"]


def test_unavailable_node_has_null_manifest():
    """Inventory entry with manifest=None is handled safely."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("orphan.mod", manifest=None, state="unavailable")
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["slug"] == "orphan.mod"
    assert node["state"] == "unavailable"
    assert "<svg" in node["icon_svg"]


def test_no_absolute_paths_in_nodes():
    """Source field has no absolute path."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", source="my-package", source_type="package")
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert not node["source"].startswith("/")


def test_svg_sanitized_in_nodes():
    """A node with icon_svg containing script tag gets sanitized."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest, ModulePresentation
    manifest = ModuleManifest(
        slug="my.module",
        label="My Module",
        presentation=ModulePresentation(
            icon_svg='<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle/></svg>',
        ),
    )
    e = _entry("my.module", manifest=manifest.to_dict())
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert "script" not in node["icon_svg"]
    assert "alert" not in node["icon_svg"]


def test_fallback_svg_for_no_icon():
    """Node with empty icon_svg gets fallback."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest, ModulePresentation
    manifest = ModuleManifest(
        slug="my.module",
        label="My Module",
        presentation=ModulePresentation(icon_svg=""),
    )
    e = _entry("my.module", manifest=manifest.to_dict())
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert "<svg" in node["icon_svg"]
    assert node["icon_svg"] != ""


def test_metadata_generated_at_is_string():
    """metadata.generated_at is a string."""
    from cauldron_module_tree.graph import build_graph
    registry = _make_registry([])
    result = build_graph(registry).to_api_dict()
    assert isinstance(result["metadata"]["generated_at"], str)
    assert len(result["metadata"]["generated_at"]) > 0


def test_metadata_restart_required_false_when_none_active():
    """No active restart modules — restart_required is False."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest
    manifest = ModuleManifest(
        slug="my.module",
        label="My Module",
        restart_required=True,
    )
    e = _entry("my.module", manifest=manifest.to_dict(), active=False)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    assert result["metadata"]["restart_required"] is False


def test_metadata_restart_required_true_when_active_restart_module():
    """Active module with requires_restart=True sets restart_required=True."""
    from cauldron_module_tree.graph import build_graph
    from cauldron.modules import ModuleManifest
    # django_apps triggers requires_restart on the manifest
    manifest = ModuleManifest(
        slug="my.module",
        label="My Module",
        django_apps=("django.contrib.auth",),
    )
    e = _entry("my.module", manifest=manifest.to_dict(), active=True)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    assert result["metadata"]["restart_required"] is True


def test_future_module_auto_discovered():
    """A module not known to the tree implementation appears in graph nodes when added to registry."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("future.unknown.mod")
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    slugs = [n["slug"] for n in result["nodes"]]
    assert "future.unknown.mod" in slugs
