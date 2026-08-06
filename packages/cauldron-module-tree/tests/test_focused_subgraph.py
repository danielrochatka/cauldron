"""Tests for ModuleGraph.focused_subgraph and FocusedModuleGraph."""
import pytest
from unittest.mock import MagicMock


def _make_registry(inventory_entries, *, capabilities=None, errors=()):
    reg = MagicMock()
    reg.inventory.return_value = list(inventory_entries)
    reg.capabilities.return_value = capabilities or {}
    reg.errors.return_value = list(errors)
    return reg


def _entry(slug, *, requires=(), optional=(), **kwargs):
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
        "requires": [{"slug": r, "kind": "module"} for r in requires],
        "optional": [{"slug": o, "kind": "module"} for o in optional],
        "deps": [],
        "django_apps": [],
        "errors": [],
        "requires_restart": False,
        "cauldron_version_ok": True,
        "installed_cauldron_version": "0.1.0",
    }
    defaults.update(kwargs)
    return defaults


def _build(entries, *, capabilities=None):
    from cauldron_module_tree.graph import build_graph
    return build_graph(_make_registry(entries, capabilities=capabilities))


# --------------------------------------------------------------------------- #
# Invalid slug                                                                 #
# --------------------------------------------------------------------------- #

def test_invalid_slug_raises():
    g = _build([_entry("a")])
    with pytest.raises(ValueError, match="not found"):
        g.focused_subgraph("does.not.exist")


# --------------------------------------------------------------------------- #
# Selected node is always included                                             #
# --------------------------------------------------------------------------- #

def test_selected_node_included():
    g = _build([_entry("a"), _entry("b")])
    f = g.focused_subgraph("a")
    assert "a" in f.nodes
    assert f.roles["a"] == "selected"


# --------------------------------------------------------------------------- #
# Transitive dependencies included                                             #
# --------------------------------------------------------------------------- #

def test_direct_dependency_included():
    g = _build([_entry("a", requires=("b",)), _entry("b")])
    f = g.focused_subgraph("a")
    assert "b" in f.nodes
    assert f.roles["b"] == "dependency"


def test_transitive_dependency_included():
    # a → b → c
    g = _build([
        _entry("a", requires=("b",)),
        _entry("b", requires=("c",)),
        _entry("c"),
    ])
    f = g.focused_subgraph("a")
    assert "b" in f.nodes
    assert "c" in f.nodes
    assert f.roles["b"] == "dependency"
    assert f.roles["c"] == "dependency"


def test_shared_dependency_appears_once():
    # a → b → d, a → c → d
    g = _build([
        _entry("a", requires=("b", "c")),
        _entry("b", requires=("d",)),
        _entry("c", requires=("d",)),
        _entry("d"),
    ])
    f = g.focused_subgraph("a")
    assert list(f.nodes.keys()).count("d") == 1
    assert f.roles["d"] == "dependency"


def test_unrelated_sibling_excluded():
    # a → b; c is unrelated
    g = _build([_entry("a", requires=("b",)), _entry("b"), _entry("c")])
    f = g.focused_subgraph("a")
    assert "c" not in f.nodes


# --------------------------------------------------------------------------- #
# Parent context                                                               #
# --------------------------------------------------------------------------- #

def test_direct_parent_included_as_context():
    # parent → selected → dep
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel", requires=("dep",)),
        _entry("dep"),
    ])
    f = g.focused_subgraph("sel")
    assert "parent" in f.nodes
    assert f.roles["parent"] == "parent_context"


def test_parent_of_parent_excluded():
    # grandparent → parent → sel
    g = _build([
        _entry("grandparent", requires=("parent",)),
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    assert "grandparent" not in f.nodes


def test_parent_unrelated_dep_excluded():
    # parent → sel; parent → unrelated
    g = _build([
        _entry("parent", requires=("sel", "unrelated")),
        _entry("sel"),
        _entry("unrelated"),
    ])
    f = g.focused_subgraph("sel")
    assert "unrelated" not in f.nodes


def test_include_direct_parents_false_excludes_parents():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel", include_direct_parents=False)
    assert "parent" not in f.nodes


# --------------------------------------------------------------------------- #
# Parent-context edges                                                         #
# --------------------------------------------------------------------------- #

def test_parent_context_edges_generated():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    pc_edges = [e for e in f.edges if e.kind == "parent_context"]
    assert len(pc_edges) == 1
    edge = pc_edges[0]
    assert edge.source == "sel"
    assert edge.target == "parent"
    assert edge.status == "resolved"


def test_parent_context_edges_serialized_with_direction_label():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    pc_edges = [e for e in d["edges"] if e["kind"] == "parent_context"]
    assert len(pc_edges) == 1
    assert pc_edges[0]["direction_label"] == "used by"


def test_optional_parent_included_as_context():
    # parent has an optional dependency on sel — still counts as a parent context
    g = _build([
        _entry("parent", optional=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    assert "parent" in f.nodes
    assert f.roles["parent"] == "parent_context"


def test_parent_context_edge_carries_relationship_kind():
    g = _build([
        _entry("req.parent", requires=("sel",)),
        _entry("opt.parent", optional=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    pc_edges = {e.target: e for e in f.edges if e.kind == "parent_context"}
    assert pc_edges["req.parent"].relationship_kind == "required"
    assert pc_edges["opt.parent"].relationship_kind == "optional"


def test_parent_context_relationship_kind_in_serialization():
    g = _build([
        _entry("req.parent", requires=("sel",)),
        _entry("sel"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    pc_edges = [e for e in d["edges"] if e["kind"] == "parent_context"]
    assert len(pc_edges) == 1
    assert pc_edges[0]["relationship_kind"] == "required"


def test_metadata_includes_missing_count():
    g = _build([_entry("a", requires=("x", "y"))])
    d = g.focused_subgraph("a").to_api_dict()
    assert d["metadata"]["missing_count"] == 2


# --------------------------------------------------------------------------- #
# Dependency edges preserved                                                   #
# --------------------------------------------------------------------------- #

def test_dependency_edges_preserved():
    g = _build([
        _entry("a", requires=("b",)),
        _entry("b", requires=("c",)),
        _entry("c"),
    ])
    f = g.focused_subgraph("a")
    dep_edges = [e for e in f.edges if e.kind != "parent_context"]
    sources = {e.source for e in dep_edges}
    targets = {e.target for e in dep_edges}
    assert "a" in sources
    assert "b" in sources
    assert "b" in targets
    assert "c" in targets


# --------------------------------------------------------------------------- #
# Cycles                                                                       #
# --------------------------------------------------------------------------- #

def test_cycles_terminate_safely():
    # a → b → a (cycle)
    g = _build([
        _entry("a", requires=("b",)),
        _entry("b", requires=("a",)),
    ])
    f = g.focused_subgraph("a")
    assert "a" in f.nodes
    assert "b" in f.nodes


# --------------------------------------------------------------------------- #
# Missing targets                                                              #
# --------------------------------------------------------------------------- #

def test_missing_target_does_not_raise():
    # a requires x but x is not registered
    g = _build([_entry("a", requires=("x",))])
    f = g.focused_subgraph("a")
    assert "a" in f.nodes
    # x is unregistered — tracked as a missing terminal, not in nodes
    assert "x" not in f.nodes
    assert "x" in f.missing_targets


# --------------------------------------------------------------------------- #
# Focus roles in serialization                                                 #
# --------------------------------------------------------------------------- #

def test_focus_roles_in_serialized_nodes():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel", requires=("dep",)),
        _entry("dep"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    role_map = {n["slug"]: n["focus_role"] for n in d["nodes"]}
    assert role_map["sel"] == "selected"
    assert role_map["dep"] == "dependency"
    assert role_map["parent"] == "parent_context"


# --------------------------------------------------------------------------- #
# Metadata                                                                     #
# --------------------------------------------------------------------------- #

def test_metadata_counts():
    g = _build([
        _entry("p1", requires=("sel",)),
        _entry("p2", requires=("sel",)),
        _entry("sel", requires=("d1", "d2")),
        _entry("d1"),
        _entry("d2"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    meta = d["metadata"]
    assert meta["selected_slug"] == "sel"
    assert meta["dependency_count"] == 2
    assert meta["parent_count"] == 2
    assert meta["max_depth"] >= 1


def test_max_depth_chain():
    # sel → d1 → d2 → d3
    g = _build([
        _entry("sel", requires=("d1",)),
        _entry("d1", requires=("d2",)),
        _entry("d2", requires=("d3",)),
        _entry("d3"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    assert d["metadata"]["max_depth"] == 3


def test_leaf_module_no_deps_no_parents():
    g = _build([_entry("leaf"), _entry("other")])
    f = g.focused_subgraph("leaf")
    assert list(f.nodes.keys()) == ["leaf"]
    assert f.roles == {"leaf": "selected"}
    d = f.to_api_dict()
    assert d["metadata"]["dependency_count"] == 0
    assert d["metadata"]["parent_count"] == 0


# --------------------------------------------------------------------------- #
# Full graph unchanged                                                         #
# --------------------------------------------------------------------------- #

def test_full_graph_unchanged_after_focused_subgraph():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel", requires=("dep",)),
        _entry("dep"),
    ])
    original_node_count = len(g.nodes)
    original_edge_count = len(g.edges)
    g.focused_subgraph("sel")
    assert len(g.nodes) == original_node_count
    assert len(g.edges) == original_edge_count


# --------------------------------------------------------------------------- #
# Determinism                                                                  #
# --------------------------------------------------------------------------- #

def test_focused_serialization_is_deterministic():
    entries = [
        _entry("p2", requires=("sel",)),
        _entry("p1", requires=("sel",)),
        _entry("sel", requires=("d2", "d1")),
        _entry("d1"),
        _entry("d2"),
    ]
    g = _build(entries)
    d1 = g.focused_subgraph("sel").to_api_dict()
    d2 = g.focused_subgraph("sel").to_api_dict()
    assert d1 == d2


# --------------------------------------------------------------------------- #
# 100-module scaling test                                                      #
# --------------------------------------------------------------------------- #

def test_scaling_100_modules():
    """Focused subgraph on a large graph includes only the correct subset."""
    import time
    # Build a 100-module chain: m0 → m1 → ... → m49; m50..m99 are unrelated
    entries = []
    for i in range(50):
        req = (f"m{i + 1}",) if i < 49 else ()
        entries.append(_entry(f"m{i}", requires=req))
    for i in range(50, 100):
        entries.append(_entry(f"m{i}"))  # disconnected

    # parent of m10 (for parent context)
    entries.append(_entry("parent.of.m10", requires=("m10",)))

    g = _build(entries)

    t0 = time.monotonic()
    f = g.focused_subgraph("m10")
    elapsed = time.monotonic() - t0

    # m10 + deps (m11..m49) = 40 dependency nodes
    dep_slugs = {f"m{i}" for i in range(11, 50)}
    assert f.roles["m10"] == "selected"
    for s in dep_slugs:
        assert s in f.nodes, f"{s} missing from focused nodes"
        assert f.roles[s] == "dependency"

    # Parent included
    assert "parent.of.m10" in f.nodes
    assert f.roles["parent.of.m10"] == "parent_context"

    # Unrelated nodes absent
    for i in range(50, 100):
        assert f"m{i}" not in f.nodes, f"m{i} should be absent"

    # m9 is the direct parent of m10 → included as parent_context
    assert "m9" in f.nodes
    assert f.roles["m9"] == "parent_context"
    # Ancestors further up the chain (m0..m8) are excluded
    for i in range(0, 9):
        assert f"m{i}" not in f.nodes, f"m{i} (ancestor) should be excluded"

    # Full graph unchanged
    assert len(g.nodes) == 101

    # Layout completes within reasonable time (pure Python BFS)
    assert elapsed < 1.0, f"focused_subgraph took {elapsed:.3f}s"


# --------------------------------------------------------------------------- #
# Missing-target serialization (synthetic node in API output)                  #
# --------------------------------------------------------------------------- #

def test_missing_target_serialized_as_synthetic_node():
    """to_api_dict() must include a synthetic node entry for each missing target."""
    g = _build([_entry("a", requires=("x.missing",))])
    d = g.focused_subgraph("a").to_api_dict()
    slugs = [n["slug"] for n in d["nodes"]]
    assert "x.missing" in slugs
    synthetic = next(n for n in d["nodes"] if n["slug"] == "x.missing")
    assert synthetic["state"] == "missing"
    assert synthetic["title"] == "Missing: x.missing"
    assert synthetic["focus_role"] == "dependency"
    assert synthetic["enabled"] is False
    assert synthetic["active"] is False
    assert synthetic.get("is_synthetic") is True


def test_missing_edges_reference_included_node():
    """Serialized edges targeting missing slugs must have a matching node."""
    g = _build([_entry("a", requires=("x.missing",))])
    d = g.focused_subgraph("a").to_api_dict()
    node_slugs = {n["slug"] for n in d["nodes"]}
    for edge in d["edges"]:
        assert edge["target"] in node_slugs, (
            f"Edge targets {edge['target']!r} which is not in serialized nodes"
        )


def test_missing_serialization_deterministic():
    """Repeated calls with missing targets produce identical output."""
    g = _build([_entry("a", requires=("z.missing", "y.missing"))])
    d1 = g.focused_subgraph("a").to_api_dict()
    d2 = g.focused_subgraph("a").to_api_dict()
    assert d1 == d2
    # Synthetic nodes appear in sorted slug order
    synthetic = [n for n in d1["nodes"] if n.get("is_synthetic")]
    assert [n["slug"] for n in synthetic] == ["y.missing", "z.missing"]
