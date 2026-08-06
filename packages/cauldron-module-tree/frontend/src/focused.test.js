/**
 * Tests for focused.js — pure focused-subgraph operations.
 *
 * New model (requires/used-by):
 *   - requires  : direct requirements of selected (ONE HOP forward only)
 *   - used_by   : full transitive reverse closure (all modules that depend on selected)
 */
import { describe, it, expect } from "vitest";
import { buildFocusedSubgraph, makeFocusedLayoutCache } from "./focused.js";

// --------------------------------------------------------------------------- //
// Test helpers                                                                 //
// --------------------------------------------------------------------------- //

function makeNode(slug, extra = {}) {
  return { slug, title: slug, state: "ready", focus_role: undefined, ...extra };
}

function makeEdge(source, target, kind = "required") {
  return { source, target, kind, capability: null, status: "resolved" };
}

function makeData(nodes, edges) {
  return { nodes, edges };
}

// --------------------------------------------------------------------------- //
// Invalid slug                                                                 //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — invalid slug", () => {
  it("throws when slug is not in graph", () => {
    const data = makeData([makeNode("a")], []);
    expect(() => buildFocusedSubgraph(data, "nope")).toThrow("not found");
  });
});

// --------------------------------------------------------------------------- //
// Selected node                                                                //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — selected", () => {
  it("selected node has role 'selected'", () => {
    const data = makeData([makeNode("a"), makeNode("b")], []);
    const result = buildFocusedSubgraph(data, "a");
    expect(result.roles["a"]).toBe("selected");
    const aNode = result.nodes.find((n) => n.slug === "a");
    expect(aNode.focus_role).toBe("selected");
  });

  it("exactly one selected role", () => {
    const data = makeData([makeNode("a"), makeNode("b")], [makeEdge("a", "b")]);
    const result = buildFocusedSubgraph(data, "a");
    const selectedCount = Object.values(result.roles).filter((r) => r === "selected").length;
    expect(selectedCount).toBe(1);
  });
});

// --------------------------------------------------------------------------- //
// Requires (direct, one hop only)                                              //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — requires (direct only)", () => {
  it("direct requirement included with role 'requires'", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req")],
      [makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["req"]).toBe("requires");
    expect(result.nodes.some((n) => n.slug === "req")).toBe(true);
  });

  it("transitive requirement NOT included (only one hop)", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("c")],
      [makeEdge("a", "b"), makeEdge("b", "c")],
    );
    const result = buildFocusedSubgraph(data, "a");
    // b is direct → requires; c is transitive → absent
    expect(result.roles["b"]).toBe("requires");
    expect(result.nodes.some((n) => n.slug === "c")).toBe(false);
    expect(result.roles["c"]).toBeUndefined();
  });

  it("unrelated node absent from focused graph", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("x")],
      [makeEdge("a", "b")],
    );
    const result = buildFocusedSubgraph(data, "a");
    expect(result.nodes.some((n) => n.slug === "x")).toBe(false);
  });

  it("multiple direct requirements all included", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("r1"), makeNode("r2")],
      [makeEdge("sel", "r1"), makeEdge("sel", "r2")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["r1"]).toBe("requires");
    expect(result.roles["r2"]).toBe("requires");
  });

  it("duplicate edges to same target deduplicated", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req")],
      [makeEdge("sel", "req", "required"), makeEdge("sel", "req", "optional")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const reqNodes = result.nodes.filter((n) => n.slug === "req");
    expect(reqNodes.length).toBe(1);
  });

  it("unrelated nodes absent from layoutEdges", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("x"), makeNode("y")],
      [makeEdge("a", "b"), makeEdge("x", "y")],
    );
    const result = buildFocusedSubgraph(data, "a");
    const layoutSlugs = new Set([
      ...result.layoutEdges.map((e) => e.source),
      ...result.layoutEdges.map((e) => e.target),
    ]);
    expect(layoutSlugs.has("x")).toBe(false);
    expect(layoutSlugs.has("y")).toBe(false);
  });
});

// --------------------------------------------------------------------------- //
// Used by (full transitive reverse closure)                                   //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — used by (transitive reverse)", () => {
  it("direct consumer included with role 'used_by'", () => {
    const data = makeData(
      [makeNode("consumer"), makeNode("sel"), makeNode("req")],
      [makeEdge("consumer", "sel"), makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["consumer"]).toBe("used_by");
    expect(result.nodes.some((n) => n.slug === "consumer")).toBe(true);
  });

  it("transitive consumer also included (grandparent of selected)", () => {
    const data = makeData(
      [makeNode("gp"), makeNode("parent"), makeNode("sel")],
      [makeEdge("gp", "parent"), makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    // Both parent and grandparent are in used_by closure
    expect(result.roles["parent"]).toBe("used_by");
    expect(result.roles["gp"]).toBe("used_by");
    expect(result.nodes.some((n) => n.slug === "gp")).toBe(true);
  });

  it("direct consumer's other deps excluded (only the closure connected to selected)", () => {
    // parent → sel; parent → unrelated (unrelated is NOT in used-by closure)
    const data = makeData(
      [makeNode("parent"), makeNode("sel"), makeNode("unrelated")],
      [makeEdge("parent", "sel"), makeEdge("parent", "unrelated")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.nodes.some((n) => n.slug === "unrelated")).toBe(false);
  });

  it("multiple direct consumers all included", () => {
    const data = makeData(
      [makeNode("c1"), makeNode("c2"), makeNode("sel")],
      [makeEdge("c1", "sel"), makeEdge("c2", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["c1"]).toBe("used_by");
    expect(result.roles["c2"]).toBe("used_by");
    const ubNodes = result.nodes.filter((n) => n.focus_role === "used_by");
    expect(ubNodes.length).toBe(2);
  });

  it("requires priority over used_by on mutual dependency (cycle)", () => {
    // sel → a (sel requires a), a → sel (a also requires sel) — mutual dependency
    const data = makeData(
      [makeNode("sel"), makeNode("a")],
      [makeEdge("sel", "a"), makeEdge("a", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    // a is a direct requires target, so requires wins over used_by
    expect(result.roles["a"]).toBe("requires");
  });
});

// --------------------------------------------------------------------------- //
// Requires edges                                                               //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — requires edges", () => {
  it("requires display edges have source=selected, target=req, kind='requires'", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req")],
      [makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const reqEdges = result.displayEdges.filter((e) => e.kind === "requires");
    expect(reqEdges.length).toBe(1);
    expect(reqEdges[0].source).toBe("sel");
    expect(reqEdges[0].target).toBe("req");
  });

  it("requires layout edges are REVERSED (req → selected) for ELK positioning", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req")],
      [makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const layoutReqEdge = result.layoutEdges.find((e) => e.kind === "requires_layout");
    expect(layoutReqEdge).toBeTruthy();
    expect(layoutReqEdge.source).toBe("req");
    expect(layoutReqEdge.target).toBe("sel");
  });

  it("requires edges absent from layoutEdges in their display form", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req")],
      [makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const reqKindInLayout = result.layoutEdges.filter((e) => e.kind === "requires");
    expect(reqKindInLayout.length).toBe(0);
  });

  it("requires edge carries relationship_kind from original edge", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("opt-req")],
      [makeEdge("sel", "opt-req", "optional")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const reqEdge = result.displayEdges.find((e) => e.kind === "requires");
    expect(reqEdge.relationship_kind).toBe("optional");
  });

  it("requires edge preserves capability and status from original edge", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("cap-provider")],
      [{ source: "sel", target: "cap-provider", kind: "capability", capability: "my.cap", status: "conflict" }],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const reqEdge = result.displayEdges.find((e) => e.kind === "requires");
    expect(reqEdge.capability).toBe("my.cap");
    expect(reqEdge.status).toBe("conflict");
  });

  it("missing requirement display edge has status 'missing'", () => {
    const data = makeData(
      [makeNode("sel")],
      [makeEdge("sel", "ghost")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const missingEdge = result.displayEdges.find((e) => e.target === "ghost");
    expect(missingEdge.status).toBe("missing");
  });
});

// --------------------------------------------------------------------------- //
// Used-by edges                                                                //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — used_by edges", () => {
  it("used_by edges are REVERSED (dependency → consumer) in both layout and display", () => {
    // consumer → sel (consumer depends on sel)
    const data = makeData(
      [makeNode("consumer"), makeNode("sel")],
      [makeEdge("consumer", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const ubEdges = result.displayEdges.filter((e) => e.kind === "used_by");
    expect(ubEdges.length).toBe(1);
    // reversed: source=sel (dependency), target=consumer
    expect(ubEdges[0].source).toBe("sel");
    expect(ubEdges[0].target).toBe("consumer");
  });

  it("used_by layout and display edges are identical", () => {
    const data = makeData(
      [makeNode("consumer"), makeNode("sel")],
      [makeEdge("consumer", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const ubDisplay = result.displayEdges.filter((e) => e.kind === "used_by");
    const ubLayout = result.layoutEdges.filter((e) => e.kind === "used_by");
    expect(ubDisplay.length).toBe(ubLayout.length);
    for (let i = 0; i < ubDisplay.length; i++) {
      expect(ubDisplay[i].source).toBe(ubLayout[i].source);
      expect(ubDisplay[i].target).toBe(ubLayout[i].target);
    }
  });

  it("layoutEdges and displayEdges have same length and share indices", () => {
    const data = makeData(
      [makeNode("consumer"), makeNode("sel"), makeNode("req")],
      [makeEdge("consumer", "sel"), makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.layoutEdges.length).toBe(result.displayEdges.length);
  });
});

// --------------------------------------------------------------------------- //
// Refocus / cycles / edge cases                                                //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — edge cases", () => {
  it("cycles terminate safely", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b")],
      [makeEdge("a", "b"), makeEdge("b", "a")],
    );
    expect(() => buildFocusedSubgraph(data, "a")).not.toThrow();
  });

  it("missing direct requirement target does not raise", () => {
    const data = makeData(
      [makeNode("a")],
      [makeEdge("a", "missing-node")],
    );
    expect(() => buildFocusedSubgraph(data, "a")).not.toThrow();
  });

  it("leaf module (no deps, no consumers) produces valid result", () => {
    const data = makeData([makeNode("leaf"), makeNode("other")], []);
    const result = buildFocusedSubgraph(data, "leaf");
    expect(result.nodes.length).toBe(1);
    expect(result.nodes[0].slug).toBe("leaf");
    expect(result.metadata.requiresCount).toBe(0);
    expect(result.metadata.usedByCount).toBe(0);
  });

  it("metadata counts are correct", () => {
    const data = makeData(
      [makeNode("c1"), makeNode("c2"), makeNode("sel"), makeNode("r1"), makeNode("r2")],
      [makeEdge("c1", "sel"), makeEdge("c2", "sel"), makeEdge("sel", "r1"), makeEdge("sel", "r2")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.requiresCount).toBe(2);
    expect(result.metadata.usedByCount).toBe(2);
  });

  it("requiresList contains names and slugs of direct requirements", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("req", { title: "My Req" })],
      [makeEdge("sel", "req")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.requiresList).toEqual([{ slug: "req", name: "My Req" }]);
  });

  it("usedByList contains names and slugs of used-by modules", () => {
    const data = makeData(
      [makeNode("consumer", { title: "My Consumer" }), makeNode("sel")],
      [makeEdge("consumer", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.usedByList).toEqual([{ slug: "consumer", name: "My Consumer" }]);
  });

  it("no 'ancestor', 'descendant', 'dependency', or 'parent_context' in metadata keys", () => {
    const data = makeData([makeNode("sel")], []);
    const result = buildFocusedSubgraph(data, "sel");
    const keys = Object.keys(result.metadata).join(" ");
    expect(keys).not.toMatch(/ancestor|descendant|dependency|parent_context/);
  });

  it("cycle bridge: consumer of a cycle-requires node still has a used-by edge", () => {
    // sel → b (requires), b → sel (cycle, b promoted to requires then dropped from usedBy),
    // c → b (c is a consumer of b — should still have an edge to b in the graph)
    const data = makeData(
      [makeNode("sel"), makeNode("b"), makeNode("c")],
      [makeEdge("sel", "b"), makeEdge("b", "sel"), makeEdge("c", "b")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    // c is in used-by closure (c→b→sel path)
    expect(result.roles["c"]).toBe("used_by");
    // The c→b edge should appear in usedByEdges (reversed: b→c) so c is connected
    const cEdge = result.displayEdges.find((e) => e.kind === "used_by" && e.target === "c");
    expect(cEdge).toBeTruthy();
    expect(cEdge.source).toBe("b");
  });
});

// --------------------------------------------------------------------------- //
// Missing requires targets                                                     //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — missing requires targets", () => {
  it("missing direct requirement creates synthetic node with role 'requires'", () => {
    const data = makeData(
      [makeNode("sel")],
      [makeEdge("sel", "ghost")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const ghost = result.nodes.find((n) => n.slug === "ghost");
    expect(ghost).toBeTruthy();
    expect(ghost.state).toBe("missing");
    expect(ghost.focus_role).toBe("requires");
    expect(result.missingTargets.has("ghost")).toBe(true);
  });

  it("missing target counted in requiresCount", () => {
    const data = makeData(
      [makeNode("sel")],
      [makeEdge("sel", "ghost")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.requiresCount).toBe(1);
    expect(result.metadata.missingCount).toBe(1);
  });

  it("missing target appears in requiresList with isMissing flag", () => {
    const data = makeData(
      [makeNode("sel")],
      [makeEdge("sel", "ghost")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.requiresList.length).toBe(1);
    expect(result.metadata.requiresList[0].slug).toBe("ghost");
    expect(result.metadata.requiresList[0].isMissing).toBe(true);
  });

  it("requiresList length matches requiresCount", () => {
    // One registered requirement and one missing
    const data = makeData(
      [makeNode("sel"), makeNode("real")],
      [makeEdge("sel", "real"), makeEdge("sel", "ghost")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.requiresList.length).toBe(result.metadata.requiresCount);
  });
});

// --------------------------------------------------------------------------- //
// Stale layout prevention (render token pattern)                              //
// --------------------------------------------------------------------------- //

describe("makeFocusedLayoutCache", () => {
  it("returns undefined for cache miss", () => {
    const cache = makeFocusedLayoutCache();
    expect(cache.get("a", "rev1")).toBeUndefined();
  });

  it("returns stored layout for cache hit", () => {
    const cache = makeFocusedLayoutCache();
    const layout = { id: "root", children: [] };
    cache.set("a", "rev1", layout);
    expect(cache.get("a", "rev1")).toBe(layout);
  });

  it("different slug gives different cache entry", () => {
    const cache = makeFocusedLayoutCache();
    cache.set("a", "rev1", { id: "a" });
    cache.set("b", "rev1", { id: "b" });
    expect(cache.get("a", "rev1").id).toBe("a");
    expect(cache.get("b", "rev1").id).toBe("b");
  });

  it("different revision gives cache miss", () => {
    const cache = makeFocusedLayoutCache();
    cache.set("a", "rev1", { id: "old" });
    expect(cache.get("a", "rev2")).toBeUndefined();
  });

  it("clear() removes all entries", () => {
    const cache = makeFocusedLayoutCache();
    cache.set("a", "rev1", {});
    cache.clear();
    expect(cache.get("a", "rev1")).toBeUndefined();
  });
});

// --------------------------------------------------------------------------- //
// 100-module scaling test (requires/used-by model)                            //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — 100-module scaling (requires/used-by)", () => {
  it("focused on m10: only m11 is requires; m0..m9 and parent.of.m10 are used_by", () => {
    // Chain: m0 → m1 → ... → m49; m50..m99 unrelated; parent.of.m10 → m10
    const nodes = [];
    const edges = [];
    for (let i = 0; i < 50; i++) {
      nodes.push(makeNode(`m${i}`));
      if (i < 49) edges.push(makeEdge(`m${i}`, `m${i + 1}`));
    }
    for (let i = 50; i < 100; i++) nodes.push(makeNode(`m${i}`));
    nodes.push(makeNode("parent.of.m10"));
    edges.push(makeEdge("parent.of.m10", "m10"));

    const data = makeData(nodes, edges);
    const result = buildFocusedSubgraph(data, "m10");

    // selected
    expect(result.roles["m10"]).toBe("selected");

    // direct requires: only m11 (one hop forward)
    expect(result.roles["m11"]).toBe("requires");

    // transitive deps (m12..m49) ABSENT in new model (only one hop shown)
    for (let i = 12; i < 50; i++) {
      expect(result.nodes.some((n) => n.slug === `m${i}`)).toBe(false);
    }

    // used_by: m9 is direct consumer of m10
    expect(result.roles["m9"]).toBe("used_by");

    // used_by: transitive consumers m0..m8 also included
    for (let i = 0; i < 9; i++) {
      expect(result.roles[`m${i}`]).toBe("used_by");
      expect(result.nodes.some((n) => n.slug === `m${i}`)).toBe(true);
    }

    // used_by: parent.of.m10
    expect(result.roles["parent.of.m10"]).toBe("used_by");

    // unrelated (m50..m99) absent
    for (let i = 50; i < 100; i++) {
      expect(result.nodes.some((n) => n.slug === `m${i}`)).toBe(false);
    }

    // Full graph data unchanged
    expect(nodes.length).toBe(101);
  });
});
