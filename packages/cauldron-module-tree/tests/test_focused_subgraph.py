"""Tests for ModuleGraph.focused_subgraph and FocusedModuleGraph.

New model (requires/used-by):
  - requires  : direct requirements of selected (ONE HOP forward only)
  - used_by   : full transitive reverse closure (all modules that depend on selected)
"""
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
# Requires: direct (one hop) only                                              #
# --------------------------------------------------------------------------- #

def test_direct_requirement_included():
    g = _build([_entry("a", requires=("b",)), _entry("b")])
    f = g.focused_subgraph("a")
    assert "b" in f.nodes
    assert f.roles["b"] == "requires"


def test_transitive_requirement_excluded():
    # a → b → c; only b is direct requires, c is absent
    g = _build([
        _entry("a", requires=("b",)),
        _entry("b", requires=("c",)),
        _entry("c"),
    ])
    f = g.focused_subgraph("a")
    assert "b" in f.nodes
    assert f.roles["b"] == "requires"
    assert "c" not in f.nodes


def test_unrelated_sibling_excluded():
    # a → b; c is unrelated
    g = _build([_entry("a", requires=("b",)), _entry("b"), _entry("c")])
    f = g.focused_subgraph("a")
    assert "c" not in f.nodes


def test_multiple_direct_requirements_included():
    g = _build([
        _entry("sel", requires=("r1", "r2")),
        _entry("r1"),
        _entry("r2"),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["r1"] == "requires"
    assert f.roles["r2"] == "requires"


# --------------------------------------------------------------------------- #
# Used-by: full transitive reverse closure                                     #
# --------------------------------------------------------------------------- #

def test_direct_consumer_included_as_used_by():
    # parent → sel → dep
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel", requires=("dep",)),
        _entry("dep"),
    ])
    f = g.focused_subgraph("sel")
    assert "parent" in f.nodes
    assert f.roles["parent"] == "used_by"


def test_grandparent_included_as_used_by():
    # grandparent → parent → sel (transitive consumer — now INCLUDED)
    g = _build([
        _entry("grandparent", requires=("parent",)),
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    assert "grandparent" in f.nodes
    assert f.roles["grandparent"] == "used_by"
    assert "parent" in f.nodes
    assert f.roles["parent"] == "used_by"


def test_consumer_unrelated_dep_excluded():
    # parent → sel; parent → unrelated (unrelated is NOT in used-by closure)
    g = _build([
        _entry("parent", requires=("sel", "unrelated")),
        _entry("sel"),
        _entry("unrelated"),
    ])
    f = g.focused_subgraph("sel")
    assert "unrelated" not in f.nodes


def test_include_used_by_false_excludes_consumers():
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel", include_used_by=False)
    assert "parent" not in f.nodes


def test_optional_consumer_included_as_used_by():
    # parent has an optional dependency on sel — still counts as a used-by consumer
    g = _build([
        _entry("parent", optional=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    assert "parent" in f.nodes
    assert f.roles["parent"] == "used_by"


# --------------------------------------------------------------------------- #
# Requires edges                                                               #
# --------------------------------------------------------------------------- #

def test_requires_edges_generated():
    g = _build([
        _entry("sel", requires=("req",)),
        _entry("req"),
    ])
    f = g.focused_subgraph("sel")
    req_edges = [e for e in f.edges if e.kind == "requires"]
    assert len(req_edges) == 1
    edge = req_edges[0]
    assert edge.source == "sel"
    assert edge.target == "req"
    assert edge.status == "resolved"


def test_requires_edge_carries_relationship_kind():
    g = _build([
        _entry("sel", requires=("req.parent",)),
        _entry("req.parent"),
    ])
    f_req = g.focused_subgraph("sel")
    req_edges = {e.target: e for e in f_req.edges if e.kind == "requires"}
    assert req_edges["req.parent"].relationship_kind == "required"


def test_optional_dep_excluded_from_focused_requires():
    # sel2 has only an optional dep on opt.req — must NOT appear in focused requires.
    g = _build([
        _entry("sel2", optional=("opt.req",)),
        _entry("opt.req"),
    ])
    f_opt = g.focused_subgraph("sel2")
    assert "opt.req" not in f_opt.roles, (
        "Optional dep must not appear under requires in focused subgraph"
    )
    opt_req_edges = [e for e in f_opt.edges if e.kind == "requires" and e.target == "opt.req"]
    assert not opt_req_edges, "No requires edge must be emitted for an optional dep"


def test_used_by_edges_preserved():
    # parent → sel; the original edge (parent → sel) should appear as used_by
    g = _build([
        _entry("parent", requires=("sel",)),
        _entry("sel"),
    ])
    f = g.focused_subgraph("sel")
    ub_edges = [e for e in f.edges if e.kind == "used_by"]
    assert len(ub_edges) >= 1
    # Original direction: source=parent, target=sel
    assert any(e.source == "parent" and e.target == "sel" for e in ub_edges)


def test_requires_edge_preserves_capability_and_status():
    # a requires b; should preserve the original edge's capability and status
    g = _build([_entry("a", requires=("b",)), _entry("b")])
    f = g.focused_subgraph("a")
    req_edges = [e for e in f.edges if e.kind == "requires" and e.target == "b"]
    assert len(req_edges) == 1
    # status should be "resolved" (both nodes exist, no errors)
    assert req_edges[0].status == "resolved"
    # relationship_kind preserves original edge kind
    assert req_edges[0].relationship_kind == "required"


def test_missing_target_edge_emitted_in_python():
    # a requires x.missing; backend should emit a "requires" edge for the missing target
    g = _build([_entry("a", requires=("x.missing",))])
    f = g.focused_subgraph("a")
    req_edges = [e for e in f.edges if e.kind == "requires"]
    assert any(e.target == "x.missing" for e in req_edges), (
        "A requires edge to the missing target must be emitted"
    )
    missing_edge = next(e for e in req_edges if e.target == "x.missing")
    assert missing_edge.status == "missing"


def test_cycle_bridge_consumer_edge_present():
    # sel → b (cycle: b also requires sel); c → b (c is a consumer of b)
    # c should appear in used_by, and a used_by edge c→b should exist
    g = _build([
        _entry("sel", requires=("b",)),
        _entry("b", requires=("sel",)),
        _entry("c", requires=("b",)),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["c"] == "used_by"
    ub_edges = [e for e in f.edges if e.kind == "used_by"]
    assert any(e.source == "c" and e.target == "b" for e in ub_edges), (
        "used_by edge c→b must be present so c is connected to the graph"
    )


def test_requires_list_includes_missing_targets():
    g = _build([_entry("a", requires=("x.missing",))])
    d = g.focused_subgraph("a").to_api_dict()
    req_list = d["metadata"]["requires_list"]
    assert any(item["slug"] == "x.missing" for item in req_list)
    missing_item = next(item for item in req_list if item["slug"] == "x.missing")
    assert missing_item.get("is_missing") is True


def test_requires_list_length_matches_requires_count():
    # One registered + one missing
    g = _build([_entry("a", requires=("b", "x.missing")), _entry("b")])
    d = g.focused_subgraph("a").to_api_dict()
    meta = d["metadata"]
    assert len(meta["requires_list"]) == meta["requires_count"]


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
# Missing targets (direct requirement to unregistered slug)                   #
# --------------------------------------------------------------------------- #

def test_missing_target_does_not_raise():
    # a requires x but x is not registered
    g = _build([_entry("a", requires=("x",))])
    f = g.focused_subgraph("a")
    assert "a" in f.nodes
    # x is unregistered — tracked as a missing terminal, not in nodes
    assert "x" not in f.nodes
    assert "x" in f.missing_targets


def test_metadata_includes_missing_count():
    g = _build([_entry("a", requires=("x", "y"))])
    d = g.focused_subgraph("a").to_api_dict()
    assert d["metadata"]["missing_count"] == 2


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
    assert role_map["dep"] == "requires"
    assert role_map["parent"] == "used_by"


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
    assert meta["requires_count"] == 2
    assert meta["used_by_count"] == 2


def test_metadata_lists_content():
    g = _build([
        _entry("consumer", requires=("sel",)),
        _entry("sel", requires=("dep",)),
        _entry("dep"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    meta = d["metadata"]
    req_slugs = [item["slug"] for item in meta["requires_list"]]
    ub_slugs = [item["slug"] for item in meta["used_by_list"]]
    assert "dep" in req_slugs
    assert "consumer" in ub_slugs


def test_leaf_module_no_deps_no_consumers():
    g = _build([_entry("leaf"), _entry("other")])
    f = g.focused_subgraph("leaf")
    assert list(f.nodes.keys()) == ["leaf"]
    assert f.roles == {"leaf": "selected"}
    d = f.to_api_dict()
    assert d["metadata"]["requires_count"] == 0
    assert d["metadata"]["used_by_count"] == 0


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
# 100-module scaling test (requires/used-by model)                            #
# --------------------------------------------------------------------------- #

def test_scaling_100_modules():
    """Focused subgraph on a large graph includes only the correct subset.

    New model: only direct requires (one hop forward); full used-by closure.
    """
    import time
    # Build a 100-module chain: m0 → m1 → ... → m49; m50..m99 are unrelated
    entries = []
    for i in range(50):
        req = (f"m{i + 1}",) if i < 49 else ()
        entries.append(_entry(f"m{i}", requires=req))
    for i in range(50, 100):
        entries.append(_entry(f"m{i}"))  # disconnected

    # parent of m10 (for used-by context)
    entries.append(_entry("parent.of.m10", requires=("m10",)))

    g = _build(entries)

    t0 = time.monotonic()
    f = g.focused_subgraph("m10")
    elapsed = time.monotonic() - t0

    # m10 is selected
    assert f.roles["m10"] == "selected"

    # Only m11 is a direct requirement (one hop)
    assert "m11" in f.nodes
    assert f.roles["m11"] == "requires"

    # Transitive deps (m12..m49) are ABSENT in the new model
    for i in range(12, 50):
        assert f"m{i}" not in f.nodes, f"m{i} (transitive dep) should be absent"

    # m9 is a direct consumer → used_by
    assert "m9" in f.nodes
    assert f.roles["m9"] == "used_by"

    # Transitive consumers m0..m8 are INCLUDED (full used-by closure)
    for i in range(0, 9):
        assert f"m{i}" in f.nodes, f"m{i} (transitive consumer) should be present"
        assert f.roles[f"m{i}"] == "used_by"

    # parent.of.m10 is a direct consumer → used_by
    assert "parent.of.m10" in f.nodes
    assert f.roles["parent.of.m10"] == "used_by"

    # Unrelated nodes absent
    for i in range(50, 100):
        assert f"m{i}" not in f.nodes, f"m{i} should be absent"

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
    assert synthetic["focus_role"] == "requires"
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


# --------------------------------------------------------------------------- #
# Transitive reduction in focused subgraph                                    #
# --------------------------------------------------------------------------- #

def test_transitive_reduction_removes_redundant_ancestor():
    # sel → b, sel → c, b → c  =>  only b in focused requires (c is redundant)
    g = _build([
        _entry("sel", requires=("b", "c")),
        _entry("b", requires=("c",)),
        _entry("c"),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["b"] == "requires"
    assert "c" not in f.roles, "c is covered transitively by b — must not appear as requires"


def test_transitive_reduction_preserves_independent_peers():
    # sel → b, sel → c, no edge b → c  =>  both b and c kept
    g = _build([
        _entry("sel", requires=("b", "c")),
        _entry("b"),
        _entry("c"),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["b"] == "requires"
    assert f.roles["c"] == "requires"


def test_transitive_reduction_longer_chain():
    # sel → b, sel → c, sel → d, b → c, c → d  =>  only b in requires
    g = _build([
        _entry("sel", requires=("b", "c", "d")),
        _entry("b", requires=("c",)),
        _entry("c", requires=("d",)),
        _entry("d"),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["b"] == "requires"
    assert "c" not in f.roles
    assert "d" not in f.roles


def test_transitive_reduction_skipped_on_cycles():
    # sel → b, b → sel (cycle): reduction must not crash or erroneously remove b
    g = _build([
        _entry("sel", requires=("b",)),
        _entry("b", requires=("sel",)),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["b"] == "requires"


def test_transitive_reduction_metadata_counts_match_reduced_set():
    # sel → b, sel → c, b → c: after reduction only b; requires_count == 1
    g = _build([
        _entry("sel", requires=("b", "c")),
        _entry("b", requires=("c",)),
        _entry("c"),
    ])
    d = g.focused_subgraph("sel").to_api_dict()
    meta = d["metadata"]
    assert meta["requires_count"] == 1
    req_slugs = [item["slug"] for item in meta["requires_list"]]
    assert "b" in req_slugs
    assert "c" not in req_slugs


def test_transitive_reduction_used_by_unaffected():
    # sel → b, sel → c, b → c; consumer → sel
    # used_by must still include consumer (transitive reduction only affects requires)
    g = _build([
        _entry("sel", requires=("b", "c")),
        _entry("b", requires=("c",)),
        _entry("c"),
        _entry("consumer", requires=("sel",)),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles["consumer"] == "used_by"


def test_optional_dep_present_in_used_by_not_requires():
    # A module optionally depends on sel: it appears in used_by (not requires of sel)
    g = _build([
        _entry("sel"),
        _entry("opt.user", optional=("sel",)),
    ])
    f = g.focused_subgraph("sel")
    assert f.roles.get("opt.user") == "used_by"
    assert "opt.user" not in [r for r, role in f.roles.items() if role == "requires"]
