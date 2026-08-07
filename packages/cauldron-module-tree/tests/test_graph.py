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
    """No pending configured-state change — restart_required is False."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", active=False)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    assert result["metadata"]["restart_required"] is False


def test_metadata_restart_required_true_when_pending_override():
    """A configured_overrides mismatch sets restart_required=True."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", enabled=True, active=True)
    registry = _make_registry([e])
    result = build_graph(registry, configured_overrides={"my.module": False}).to_api_dict()
    assert result["metadata"]["restart_required"] is True


def test_future_module_auto_discovered():
    """A module not known to the tree implementation appears in graph nodes when added to registry."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("future.unknown.mod")
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    slugs = [n["slug"] for n in result["nodes"]]
    assert "future.unknown.mod" in slugs


def test_missing_dep_target_does_not_raise_key_error():
    """to_api_dict() must not raise KeyError when an edge target is missing from nodes."""
    from cauldron_module_tree.graph import build_graph
    # A requires B, but B is not in inventory — creates a "missing" edge
    a = _entry("mod.a", requires=[{"slug": "mod.missing", "kind": "module"}])
    registry = _make_registry([a])
    result = build_graph(registry).to_api_dict()  # must not raise
    assert len(result["nodes"]) == 1
    missing_edges = [e for e in result["edges"] if e["status"] == "missing"]
    assert len(missing_edges) == 1


def test_configured_overrides_propagate_to_nodes():
    """configured_enabled reflects overlay state independently from runtime enabled."""
    from cauldron_module_tree.graph import build_graph
    # Module is active at runtime (enabled=True, active=True)
    e = _entry("mod.a", enabled=True, active=True)
    registry = _make_registry([e])
    # Overlay says it should be disabled (pending restart)
    graph = build_graph(registry, configured_overrides={"mod.a": False})
    result = graph.to_api_dict()
    node = result["nodes"][0]
    assert node["runtime_enabled"] is True     # still running
    assert node["configured_enabled"] is False  # pending disable after restart


def test_configured_overrides_absent_falls_back_to_enabled():
    """Without configured_overrides, configured_enabled mirrors enabled."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("mod.a", enabled=True, active=True)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert node["configured_enabled"] is True
    assert node["runtime_enabled"] is True


def test_100_node_synthetic_graph():
    """100-module graph with shared deps, cycle, missing targets, disconnected components.

    Verifies: correct node count, missing edges, cycle detection, multiple
    connected components, correct parent/child mapping, deterministic output,
    and no KeyError from to_api_dict().
    """
    from cauldron_module_tree.graph import build_graph

    # All slugs use letter-prefixed segments to satisfy [a-z][a-z0-9]* per segment.
    def dep(i):     return f"syn.dep.d{i}"
    def shared(i):  return f"syn.shared.s{i}"
    def missing(i): return f"syn.missing.m{i}"
    def islanda(i): return f"syn.island.ia{i}"
    def islandb(i): return f"syn.island.ib{i}"
    def node(i):    return f"syn.node.n{i}"

    entries = []

    # Chain: dep.d0 → dep.d1 → ... → dep.d9 (d0 requires d1, etc.)
    for i in range(10):
        requires = [{"slug": dep(i + 1), "kind": "module"}] if i < 9 else []
        entries.append(_entry(dep(i), requires=requires))

    # Fan-in: shared.s0 … shared.s9 all require dep.d0
    for i in range(10):
        entries.append(_entry(shared(i), requires=[{"slug": dep(0), "kind": "module"}]))

    # Cycle: cycle.a → cycle.b → cycle.c → cycle.a
    entries.append(_entry("syn.cycle.a", requires=[{"slug": "syn.cycle.b", "kind": "module"}]))
    entries.append(_entry("syn.cycle.b", requires=[{"slug": "syn.cycle.c", "kind": "module"}]))
    entries.append(_entry("syn.cycle.c", requires=[{"slug": "syn.cycle.a", "kind": "module"}]))

    # Missing targets: 7 modules each require a non-existent slug
    for i in range(7):
        entries.append(_entry(missing(i), requires=[{"slug": "syn.nonexistent", "kind": "module"}]))

    # Disconnected island pairs: ia.N ← ib.N, no connection to main graph
    for i in range(15):
        entries.append(_entry(islanda(i)))
        entries.append(_entry(islandb(i), requires=[{"slug": islanda(i), "kind": "module"}]))

    # Remaining nodes fanning into the chain
    for i in range(60, 100):
        entries.append(_entry(node(i), requires=[{"slug": dep((i * 3) % 10), "kind": "module"}]))

    assert len(entries) == 100

    registry = _make_registry(entries)
    graph = build_graph(registry)

    result = graph.to_api_dict()  # must not raise

    # Basic counts
    assert len(result["nodes"]) == 100
    assert len(result["edges"]) > 0

    # Missing edges present for the 7 missing-target modules
    missing_edges = [e for e in result["edges"] if e["status"] == "missing"]
    assert len(missing_edges) == 7

    # Cycles detected (the 3-node cycle a→b→c→a)
    cycles = result["metadata"]["cycles"]
    assert len(cycles) > 0
    cycle_slugs = {slug for cycle in cycles for slug in cycle}
    assert {"syn.cycle.a", "syn.cycle.b", "syn.cycle.c"}.issubset(cycle_slugs)

    # Disconnected components (islands are separate from the main chain)
    assert result["metadata"]["components_count"] > 1

    # Parent/child mapping: dep.d0 should have shared.s0…s9 as parents
    node_map = {n["slug"]: n for n in result["nodes"]}
    dep0_parents = set(node_map[dep(0)]["parents"])
    for i in range(10):
        assert shared(i) in dep0_parents, f"{shared(i)} missing from {dep(0)} parents"

    # dep.d0 children should include dep.d1 (dep.d0 requires dep.d1)
    dep0_children = set(node_map[dep(0)]["children"])
    assert dep(1) in dep0_children

    # Deterministic serialisation
    result2 = graph.to_api_dict()
    assert result["nodes"] == result2["nodes"]
    assert result["edges"] == result2["edges"]


def test_pending_restart_set_when_configured_differs_from_enabled():
    """configured_enabled != enabled produces pending_restart=True on the node."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", enabled=True, active=True)
    registry = _make_registry([e])
    graph = build_graph(registry, configured_overrides={"my.module": False})
    result = graph.to_api_dict()
    node = result["nodes"][0]
    assert node["pending_restart"] is True


def test_pending_restart_false_when_no_override():
    """Without any override, pending_restart is False."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", enabled=True, active=True)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert node["pending_restart"] is False


def test_pending_restart_count_in_metadata():
    """pending_restart_count counts modules with a pending change."""
    from cauldron_module_tree.graph import build_graph
    a = _entry("mod.a", enabled=True, active=True)
    b = _entry("mod.b", enabled=True, active=True)
    c = _entry("mod.c", enabled=True, active=True)
    registry = _make_registry([a, b, c])
    result = build_graph(
        registry,
        configured_overrides={"mod.a": False, "mod.b": False},
    ).to_api_dict()
    assert result["metadata"]["pending_restart_count"] == 2


def test_inactive_enabled_differs_from_disabled():
    """enabled=True, active=False is NOT the same as disabled in the API dict."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", enabled=True, active=False)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert node["enabled"] is True
    assert node["active"] is False


def test_configured_state_survives_serialization():
    """configured_enabled is present in every node of the API response."""
    from cauldron_module_tree.graph import build_graph
    entries = [_entry(f"mod.n{i}", enabled=True, active=True) for i in range(5)]
    registry = _make_registry(entries)
    result = build_graph(registry).to_api_dict()
    for node in result["nodes"]:
        assert "configured_enabled" in node


def test_absent_overlay_falls_back_to_enabled():
    """build_graph with no configured_overrides: configured_enabled == enabled."""
    from cauldron_module_tree.graph import build_graph
    e = _entry("my.module", enabled=False, active=False)
    registry = _make_registry([e])
    result = build_graph(registry).to_api_dict()
    node = result["nodes"][0]
    assert node["configured_enabled"] == node["enabled"]
    assert node["pending_restart"] is False


# --------------------------------------------------------------------------- #
# Transitive reduction in full graph API output                                #
# --------------------------------------------------------------------------- #

def test_full_graph_transitive_reduction_removes_redundant_edge():
    """A→B, B→C, A→C: canonical output must omit A→C (redundant via B)."""
    from cauldron_module_tree.graph import build_graph
    entries = [
        _entry("a", requires=[{"slug": "b", "kind": "module"}, {"slug": "c", "kind": "module"}]),
        _entry("b", requires=[{"slug": "c", "kind": "module"}]),
        _entry("c"),
    ]
    g = build_graph(_make_registry(entries))
    result = g.to_api_dict()
    req_edges = [(e["source"], e["target"]) for e in result["edges"] if e["kind"] == "required"]
    assert ("a", "b") in req_edges
    assert ("b", "c") in req_edges
    assert ("a", "c") not in req_edges, "A→C is redundant (A→B→C) and must be omitted"


def test_full_graph_optional_edges_not_reduced():
    """Optional edges are always included regardless of transitive coverage."""
    from cauldron_module_tree.graph import build_graph
    entries = [
        _entry("a",
               requires=[{"slug": "b", "kind": "module"}],
               optional=[{"slug": "c", "kind": "module"}]),
        _entry("b", requires=[{"slug": "c", "kind": "module"}]),
        _entry("c"),
    ]
    g = build_graph(_make_registry(entries))
    result = g.to_api_dict()
    opt_edges = [(e["source"], e["target"]) for e in result["edges"] if e["kind"] == "optional"]
    assert ("a", "c") in opt_edges, "Optional A→C must remain in output"


def test_full_graph_transitive_reduction_parents_children():
    """parents/children fields in node output reflect the reduced graph."""
    from cauldron_module_tree.graph import build_graph
    entries = [
        _entry("a", requires=[{"slug": "b", "kind": "module"}, {"slug": "c", "kind": "module"}]),
        _entry("b", requires=[{"slug": "c", "kind": "module"}]),
        _entry("c"),
    ]
    g = build_graph(_make_registry(entries))
    result = g.to_api_dict()
    nodes = {n["slug"]: n for n in result["nodes"]}
    # a's children: only b (c is redundant)
    assert nodes["a"]["children"] == ["b"]
    # c's parents: only b (a→c is removed)
    assert nodes["c"]["parents"] == ["b"]


def test_full_graph_no_duplicate_edges_same_target():
    """When capability + required both target the same module, only one edge appears."""
    from cauldron_module_tree.graph import build_graph

    def _cap_entry(slug, *, module_requires=(), cap_requires=(), provides=(), **kwargs):
        from cauldron.modules import ModuleManifest
        manifest = ModuleManifest(slug=slug, label=slug)
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
            "provides": list(provides),
            "requires": (
                [{"slug": r, "kind": "module"} for r in module_requires] +
                [{"slug": c, "kind": "capability"} for c in cap_requires]
            ),
            "optional": [],
            "selected_providers": {},
            "deps": [],
            "django_apps": [],
            "errors": [],
            "requires_restart": False,
            "cauldron_version_ok": True,
            "installed_cauldron_version": "0.1.0",
        }
        defaults.update(kwargs)
        return defaults

    entries = [
        _cap_entry("a", module_requires=["b"], cap_requires=["my.cap"]),
        _cap_entry("b", provides=["my.cap"]),
    ]
    g = build_graph(_make_registry(entries, capabilities={"my.cap": ["b"]}))
    result = g.to_api_dict()
    edges_to_b = [e for e in result["edges"]
                  if e["source"] == "a" and e["target"] == "b"
                  and e["kind"] in ("required", "capability")]
    assert len(edges_to_b) == 1, f"Expected 1 edge a→b, got {len(edges_to_b)}: {edges_to_b}"


def test_transitive_reduction_stable_output():
    """Repeated calls produce identical edge output (deterministic)."""
    from cauldron_module_tree.graph import build_graph
    entries = [
        _entry("a", requires=[{"slug": "b", "kind": "module"}, {"slug": "c", "kind": "module"}]),
        _entry("b", requires=[{"slug": "c", "kind": "module"}]),
        _entry("c"),
    ]
    g = build_graph(_make_registry(entries))
    r1 = g.to_api_dict()
    r2 = g.to_api_dict()
    assert r1["edges"] == r2["edges"]
