/**
 * Tests for focused.js — pure focused-subgraph operations.
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
// Dependencies                                                                 //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — dependencies", () => {
  it("direct dependency included with role 'dependency'", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("dep")],
      [makeEdge("sel", "dep")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["dep"]).toBe("dependency");
    expect(result.nodes.some((n) => n.slug === "dep")).toBe(true);
  });

  it("transitive dependency included", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("c")],
      [makeEdge("a", "b"), makeEdge("b", "c")],
    );
    const result = buildFocusedSubgraph(data, "a");
    expect(result.roles["c"]).toBe("dependency");
  });

  it("unrelated node absent from focused graph", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("x")],
      [makeEdge("a", "b")],
    );
    const result = buildFocusedSubgraph(data, "a");
    expect(result.nodes.some((n) => n.slug === "x")).toBe(false);
  });

  it("shared dependency appears once", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("c"), makeNode("shared")],
      [makeEdge("a", "b"), makeEdge("a", "c"), makeEdge("b", "shared"), makeEdge("c", "shared")],
    );
    const result = buildFocusedSubgraph(data, "a");
    const sharedCount = result.nodes.filter((n) => n.slug === "shared").length;
    expect(sharedCount).toBe(1);
  });

  it("unrelated nodes absent from ELK input (layoutEdges)", () => {
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
// Parent context                                                               //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — parent context", () => {
  it("direct parent included with role 'parent_context'", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel"), makeNode("dep")],
      [makeEdge("parent", "sel"), makeEdge("sel", "dep")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.roles["parent"]).toBe("parent_context");
    expect(result.nodes.some((n) => n.slug === "parent")).toBe(true);
  });

  it("parent of parent excluded", () => {
    const data = makeData(
      [makeNode("gp"), makeNode("parent"), makeNode("sel")],
      [makeEdge("gp", "parent"), makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.nodes.some((n) => n.slug === "gp")).toBe(false);
  });

  it("parent context nodes present in focused nodes list", () => {
    const data = makeData(
      [makeNode("p1"), makeNode("p2"), makeNode("sel")],
      [makeEdge("p1", "sel"), makeEdge("p2", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const parentNodes = result.nodes.filter((n) => n.focus_role === "parent_context");
    expect(parentNodes.length).toBe(2);
  });

  it("parent node has reduced role (parent_context) in focused nodes", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel")],
      [makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const parentNode = result.nodes.find((n) => n.slug === "parent");
    expect(parentNode?.focus_role).toBe("parent_context");
  });
});

// --------------------------------------------------------------------------- //
// Parent-context edges                                                         //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — parent_context edges", () => {
  it("parent_context edges appear in displayEdges", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel")],
      [makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const pcEdges = result.displayEdges.filter((e) => e.kind === "parent_context");
    expect(pcEdges.length).toBe(1);
    expect(pcEdges[0].source).toBe("sel");
    expect(pcEdges[0].target).toBe("parent");
    expect(pcEdges[0].direction_label).toBe("used by");
  });

  it("parent_context edges absent from layoutEdges (ELK receives reversed layout edges)", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel")],
      [makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const pcEdges = result.layoutEdges.filter((e) => e.kind === "parent_context");
    expect(pcEdges.length).toBe(0);
  });

  it("ELK receives reversed layout edge (parent → sel) for positioning", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel")],
      [makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const layoutPcEdge = result.layoutEdges.find((e) => e.kind === "parent_context_layout");
    expect(layoutPcEdge).toBeTruthy();
    expect(layoutPcEdge.source).toBe("parent");
    expect(layoutPcEdge.target).toBe("sel");
  });

  it("parent context arrows point upward semantically (source=selected, target=parent)", () => {
    const data = makeData(
      [makeNode("parent"), makeNode("sel")],
      [makeEdge("parent", "sel")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    const pcEdge = result.displayEdges.find((e) => e.kind === "parent_context");
    expect(pcEdge.source).toBe("sel");
    expect(pcEdge.target).toBe("parent");
  });
});

// --------------------------------------------------------------------------- //
// ELK is called for focused view (verified via layoutEdges presence)          //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — ELK input", () => {
  it("layoutEdges does not include original required edge for parent nodes", () => {
    const data = makeData(
      [makeNode("a"), makeNode("b"), makeNode("c")],
      [makeEdge("a", "b"), makeEdge("b", "c"), makeEdge("a", "c")],
    );
    const result = buildFocusedSubgraph(data, "b");
    // "b" only has dep "c"; "a" is parent
    // The original required a→b edge should NOT appear as kind="required" in layoutEdges
    const requiredAtoB = result.layoutEdges.find(
      (e) => e.source === "a" && e.target === "b" && e.kind === "required",
    );
    expect(requiredAtoB).toBeUndefined();
    // A parent_context_layout edge (a→b) IS present for ELK to position a above b
    const layoutAtoB = result.layoutEdges.find(
      (e) => e.source === "a" && e.target === "b" && e.kind === "parent_context_layout",
    );
    expect(layoutAtoB).toBeTruthy();
    // Dependency edge b→c is present
    expect(result.layoutEdges.some((e) => e.source === "b" && e.target === "c")).toBe(true);
  });

  it("dependencies are present in layoutEdges", () => {
    const data = makeData(
      [makeNode("sel"), makeNode("dep1"), makeNode("dep2")],
      [makeEdge("sel", "dep1"), makeEdge("sel", "dep2")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.layoutEdges.some((e) => e.source === "sel" && e.target === "dep1")).toBe(true);
    expect(result.layoutEdges.some((e) => e.source === "sel" && e.target === "dep2")).toBe(true);
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

  it("missing dependency target does not raise", () => {
    const data = makeData(
      [makeNode("a")],
      [makeEdge("a", "missing-node")],
    );
    expect(() => buildFocusedSubgraph(data, "a")).not.toThrow();
  });

  it("leaf module (no deps, no parents) produces valid result", () => {
    const data = makeData([makeNode("leaf"), makeNode("other")], []);
    const result = buildFocusedSubgraph(data, "leaf");
    expect(result.nodes.length).toBe(1);
    expect(result.nodes[0].slug).toBe("leaf");
    expect(result.metadata.dependencyCount).toBe(0);
    expect(result.metadata.parentCount).toBe(0);
  });

  it("metadata counts are correct", () => {
    const data = makeData(
      [makeNode("p1"), makeNode("p2"), makeNode("sel"), makeNode("d1"), makeNode("d2")],
      [makeEdge("p1", "sel"), makeEdge("p2", "sel"), makeEdge("sel", "d1"), makeEdge("sel", "d2")],
    );
    const result = buildFocusedSubgraph(data, "sel");
    expect(result.metadata.dependencyCount).toBe(2);
    expect(result.metadata.parentCount).toBe(2);
    expect(result.metadata.maxDepth).toBe(1);
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
// 100-module scaling test                                                      //
// --------------------------------------------------------------------------- //

describe("buildFocusedSubgraph — 100-module scaling", () => {
  it("focused on m10 in 101-module graph contains only correct subset", () => {
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

    // direct parent
    expect(result.roles["parent.of.m10"]).toBe("parent_context");

    // transitive deps (m11..m49)
    for (let i = 11; i < 50; i++) {
      expect(result.roles[`m${i}`]).toBe("dependency");
    }

    // m9 is direct parent of m10
    expect(result.roles["m9"]).toBe("parent_context");

    // unrelated (m50..m99) absent
    for (let i = 50; i < 100; i++) {
      expect(result.nodes.some((n) => n.slug === `m${i}`)).toBe(false);
    }

    // ancestors (m0..m8) excluded
    for (let i = 0; i < 9; i++) {
      expect(result.nodes.some((n) => n.slug === `m${i}`)).toBe(false);
    }

    // Full graph data unchanged
    expect(nodes.length).toBe(101);
  });
});
