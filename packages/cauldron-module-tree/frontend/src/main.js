/**
 * cauldron-module-tree production bundle.
 * Loaded by the Django template via {% static 'cauldron_module_tree/module-tree.js' %}.
 * Reads data-* attributes from #tree-root, fetches the graph API, runs ELK
 * for layered orthogonal layout, and renders the interactive graph.
 */
import ELK from "elkjs/lib/elk.bundled.js";
import { renderGraph } from "./graph-renderer.js";
import { initInteraction } from "./interaction.js";
import { slugColor } from "./colors.js";

const ELK_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.spacing.nodeNodeBetweenLayers": "80",
  "elk.spacing.nodeNode": "40",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
};

async function init() {
  const root = document.getElementById("tree-root");
  if (!root) return;

  const graphUrl = root.dataset.graphUrl;
  const canChange = root.dataset.canChange === "1";

  // Show loading state
  root.innerHTML = `<div class="tree-loading" role="status">
    <div class="spinner" aria-label="Loading..."></div>
    <span>Loading module dependency tree…</span>
  </div>`;

  let graphData;
  try {
    const resp = await fetch(graphUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    graphData = await resp.json();
  } catch (e) {
    root.innerHTML = `<div class="tree-error" role="alert">
      <strong>Failed to load module graph:</strong> ${escapeHtml(e.message)}
    </div>`;
    return;
  }

  if (!graphData.nodes?.length) {
    root.innerHTML = `<div class="tree-empty">No modules registered.</div>`;
    return;
  }

  try {
    const elk = new ELK();
    const elkGraph = buildElkGraph(graphData);
    const layout = await elk.layout(elkGraph, { layoutOptions: ELK_OPTIONS });
    root.innerHTML = "";
    const app = renderGraph(root, layout, graphData, { canChange, slugColor });
    initInteraction(app, root, graphData, { canChange });
  } catch (e) {
    console.error("ELK layout error:", e);
    root.innerHTML = `<div class="tree-error" role="alert">
      <strong>Graph layout failed:</strong> ${escapeHtml(e.message)}
    </div>`;
  }
}

function buildElkGraph(graphData) {
  const NODE_W = 200, NODE_H = 72;
  return {
    id: "root",
    children: graphData.nodes.map((n) => ({
      id: n.slug,
      width: NODE_W,
      height: NODE_H,
      labels: [{ text: n.title || n.slug }],
      layoutOptions: {
        "elk.portConstraints": "FIXED_ORDER",
      },
      ports: [
        // One dedicated output port per outgoing edge (keeps lanes separate)
        ...outgoingEdgesFor(n.slug, graphData.edges).map((e, i) => ({
          id: `${n.slug}__out__${i}`,
          properties: { "port.side": "SOUTH", "port.index": String(i) },
        })),
        // One dedicated input port per incoming edge
        ...incomingEdgesFor(n.slug, graphData.edges).map((e, i) => ({
          id: `${n.slug}__in__${i}`,
          properties: { "port.side": "NORTH", "port.index": String(i) },
        })),
      ],
    })),
    edges: graphData.edges
      .filter((e) => e.status !== "missing")
      .map((e, idx) => {
        const outIdx = outgoingEdgesFor(e.source, graphData.edges).findIndex(
          (x) => x === e || (x.source === e.source && x.target === e.target && x.kind === e.kind)
        );
        const inIdx = incomingEdgesFor(e.target, graphData.edges).findIndex(
          (x) => x === e || (x.source === e.source && x.target === e.target && x.kind === e.kind)
        );
        return {
          id: `edge_${idx}`,
          sources: [`${e.source}__out__${Math.max(0, outIdx)}`],
          targets: [`${e.target}__in__${Math.max(0, inIdx)}`],
          labels: e.capability ? [{ text: e.capability }] : [],
          data: e,
        };
      }),
  };
}

function outgoingEdgesFor(slug, edges) {
  return edges.filter((e) => e.source === slug);
}
function incomingEdgesFor(slug, edges) {
  return edges.filter((e) => e.target === slug);
}
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
