/**
 * cauldron-module-tree production bundle.
 * Loaded by the Django template via {% static 'cauldron_module_tree/module-tree.js' %}.
 *
 * Two rendering modes:
 *   full     – complete system graph; ELK layout of all modules
 *   focused  – reduced graph around one selected module; fresh ELK layout of
 *              just the focused subset (selected + transitive deps + direct parents)
 */
import ELK from "elkjs/lib/elk.bundled.js";
import { renderGraph } from "./graph-renderer.js";
import { initInteraction } from "./interaction.js";
import { slugColor } from "./colors.js";
import { buildFocusedSubgraph, makeFocusedLayoutCache } from "./focused.js";

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

  showLoading(root, "Loading module dependency tree…");

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

  // Populate group filter once from immutable full graph data.
  // interaction.js only binds the change listener; it never appends options.
  populateGroupFilter(graphData.nodes);

  // Monotonically increasing token; stale ELK results check against this.
  let renderToken = 0;
  const layoutCache = makeFocusedLayoutCache();
  const graphRevision = graphData.metadata?.generated_at ?? Date.now();

  // Holds the currently mounted interaction so it can be disposed before redraw.
  const interactionRef = { current: null };

  const controller = {
    async enterFocus(slug, { historyMode = "push" } = {}) {
      const token = ++renderToken;
      announceMode(`Focused on ${slug}`);
      updateUrl(slug, historyMode);
      updateBreadcrumb(slug, graphData);
      showLoading(root, `Building focused graph for ${slug}…`);
      try {
        await renderFocused(root, graphData, slug, canChange, layoutCache, graphRevision, token, () => renderToken, controller, interactionRef);
      } catch (e) {
        if (renderToken !== token) return;  // stale
        root.innerHTML = `<div class="tree-error" role="alert">
          <strong>Focused layout failed:</strong> ${escapeHtml(e.message)}
        </div>`;
      }
    },
    async exitFocus({ historyMode = "push" } = {}) {
      const token = ++renderToken;
      announceMode("Full module graph");
      updateUrl(null, historyMode);
      updateBreadcrumb(null, graphData);
      showLoading(root, "Loading full module graph…");
      try {
        await renderFull(root, graphData, canChange, token, () => renderToken, controller, interactionRef);
      } catch (e) {
        if (renderToken !== token) return;
        root.innerHTML = `<div class="tree-error" role="alert">
          <strong>Full graph layout failed:</strong> ${escapeHtml(e.message)}
        </div>`;
      }
    },
  };

  // Bind focused toolbar buttons once — their HTML is stable and never replaced.
  document.getElementById("btn-show-all-focused")?.addEventListener("click", () => controller.exitFocus());
  document.getElementById("btn-fit-focused")?.addEventListener("click", () => interactionRef.current?.fitToView());

  // Handle browser back/forward — never push/replace state from popstate
  window.addEventListener("popstate", (ev) => {
    const slug = ev.state?.focus ?? getFocusFromUrl();
    if (slug) controller.enterFocus(slug, { historyMode: "none" });
    else controller.exitFocus({ historyMode: "none" });
  });

  // Initial render: check URL for ?focus=slug
  const initialFocus = getFocusFromUrl();
  if (initialFocus) {
    const exists = graphData.nodes.some((n) => n.slug === initialFocus);
    if (exists) {
      updateBreadcrumb(initialFocus, graphData);
      announceMode(`Focused on ${initialFocus}`);
      await renderFocused(root, graphData, initialFocus, canChange, layoutCache, graphRevision, ++renderToken, () => renderToken, controller, interactionRef);
    } else {
      // Invalid slug: fall back to full graph with a message
      showInvalidFocusMessage(root, initialFocus);
      updateUrl(null, "replace");
      await renderFull(root, graphData, canChange, ++renderToken, () => renderToken, controller, interactionRef);
    }
  } else {
    updateBreadcrumb(null, graphData);
    await renderFull(root, graphData, canChange, ++renderToken, () => renderToken, controller, interactionRef);
  }
}

// --------------------------------------------------------------------------- //
// Render modes                                                                 //
// --------------------------------------------------------------------------- //

async function renderFull(root, graphData, canChange, token, getToken, controller, interactionRef) {
  const elk = new ELK();
  const elkGraph = buildElkGraph(graphData.nodes, graphData.edges);
  const layout = await elk.layout(elkGraph, { layoutOptions: ELK_OPTIONS });
  if (getToken() !== token) return;  // stale: a newer focus/defocus was triggered
  interactionRef.current?.dispose();
  root.innerHTML = "";
  const app = renderGraph(root, layout, graphData, { canChange, slugColor });
  interactionRef.current = initInteraction(app, root, graphData, {
    canChange,
    onEnterFocus: controller?.enterFocus.bind(controller),
    onExitFocus: controller?.exitFocus.bind(controller),
  });
  // Restore normal toolbar visibility
  document.getElementById("tree-toolbar")?.style?.removeProperty("display");
  document.getElementById("focused-toolbar")?.style?.setProperty("display", "none");
}

async function renderFocused(root, graphData, slug, canChange, layoutCache, graphRevision, token, getToken, controller, interactionRef) {
  // Derive focused subgraph from already-loaded data
  const focused = buildFocusedSubgraph(graphData, slug);

  // Check layout cache
  let layout = layoutCache.get(slug, graphRevision);
  if (!layout) {
    const elk = new ELK();
    const elkGraph = buildElkGraph(focused.nodes, focused.layoutEdges);
    layout = await elk.layout(elkGraph, { layoutOptions: ELK_OPTIONS });
    layoutCache.set(slug, graphRevision, layout);
  }

  if (getToken() !== token) return;  // stale
  interactionRef.current?.dispose();

  // Assemble a graphData-shaped object for renderGraph
  const focusedGraphData = {
    ...graphData,
    nodes: focused.nodes,
    edges: focused.displayEdges,
  };

  root.innerHTML = "";
  const app = renderGraph(root, layout, focusedGraphData, {
    canChange,
    slugColor,
    focusConfig: {
      roles: focused.roles,
      displayEdges: focused.displayEdges,
    },
  });

  // Re-init interaction with the focused graph and pass controller callbacks
  // so clicking any node (dep or parent) re-focuses around it.
  interactionRef.current = initInteraction(app, root, focusedGraphData, {
    canChange,
    focusedSlug: slug,
    fullGraphData: graphData,
    onEnterFocus: controller?.enterFocus.bind(controller),
    onExitFocus: controller?.exitFocus.bind(controller),
  });

  // Update focused stats in toolbar (buttons are stable — only stats div changes)
  updateFocusedToolbar(focused.metadata);
}

// --------------------------------------------------------------------------- //
// ELK graph builder                                                            //
// --------------------------------------------------------------------------- //

function buildElkGraph(nodes, edges) {
  const NODE_W = 200, NODE_H = 72;
  const nodeSet = new Set(nodes.map((n) => n.slug));

  return {
    id: "root",
    children: nodes.map((n) => ({
      id: n.slug,
      width: NODE_W,
      height: NODE_H,
      labels: [{ text: n.title || n.slug }],
      layoutOptions: { "elk.portConstraints": "FIXED_ORDER" },
      ports: [
        ...outgoingEdgesFor(n.slug, edges, nodeSet).map((_, i) => ({
          id: `${n.slug}__out__${i}`,
          properties: { "port.side": "SOUTH", "port.index": String(i) },
        })),
        ...incomingEdgesFor(n.slug, edges, nodeSet).map((_, i) => ({
          id: `${n.slug}__in__${i}`,
          properties: { "port.side": "NORTH", "port.index": String(i) },
        })),
      ],
    })),
    edges: edges
      .map((e, originalIdx) => ({ e, originalIdx }))
      .filter(({ e }) => nodeSet.has(e.source) && nodeSet.has(e.target))
      .map(({ e, originalIdx }) => {
        const outIdx = outgoingEdgesFor(e.source, edges, nodeSet).findIndex(
          (x) => x === e || (x.source === e.source && x.target === e.target && x.kind === e.kind)
        );
        const inIdx = incomingEdgesFor(e.target, edges, nodeSet).findIndex(
          (x) => x === e || (x.source === e.source && x.target === e.target && x.kind === e.kind)
        );
        return {
          id: `edge_${originalIdx}`,
          sources: [`${e.source}__out__${Math.max(0, outIdx)}`],
          targets: [`${e.target}__in__${Math.max(0, inIdx)}`],
          labels: e.capability ? [{ text: e.capability }] : [],
          data: e,
        };
      }),
  };
}

function outgoingEdgesFor(slug, edges, nodeSet) {
  return edges.filter((e) => e.source === slug && (!nodeSet || nodeSet.has(e.target)));
}
function incomingEdgesFor(slug, edges, nodeSet) {
  return edges.filter((e) => e.target === slug && (!nodeSet || nodeSet.has(e.source)));
}

// --------------------------------------------------------------------------- //
// Breadcrumb                                                                   //
// --------------------------------------------------------------------------- //

function updateBreadcrumb(slug, graphData) {
  const bc = document.getElementById("tree-breadcrumb");
  if (!bc) return;
  if (!slug) {
    bc.innerHTML = '<span class="bc-current">All modules</span>';
    bc.setAttribute("aria-label", "Breadcrumb: all modules");
  } else {
    const node = graphData.nodes.find((n) => n.slug === slug);
    const title = node?.title || slug;
    bc.innerHTML = `<button class="bc-all" id="bc-all-btn" type="button">All modules</button>
      <span class="bc-sep" aria-hidden="true">›</span>
      <span class="bc-current" aria-current="page">${escapeHtml(title)}</span>`;
    bc.setAttribute("aria-label", `Breadcrumb: all modules / ${title}`);
  }
}

// --------------------------------------------------------------------------- //
// Focused toolbar                                                              //
// --------------------------------------------------------------------------- //

function updateFocusedToolbar(meta) {
  const toolbar = document.getElementById("focused-toolbar");
  if (!toolbar) return;
  toolbar.style.display = "flex";
  // Only update the stats section — buttons are stable and must not be replaced.
  const stats = document.getElementById("focused-toolbar-stats");
  if (stats) {
    stats.innerHTML = `
      <span class="focused-stat"><strong>${escapeHtml(meta.selected)}</strong></span>
      <span class="focused-stat">${meta.dependencyCount} dep${meta.dependencyCount !== 1 ? "s" : ""}</span>
      <span class="focused-stat">${meta.parentCount} parent${meta.parentCount !== 1 ? "s" : ""}</span>
      <span class="focused-stat">depth ${meta.maxDepth}</span>
    `;
  }
  document.getElementById("tree-toolbar")?.style?.setProperty("display", "none");
}

// --------------------------------------------------------------------------- //
// URL / history helpers                                                        //
// --------------------------------------------------------------------------- //

function getFocusFromUrl() {
  return new URLSearchParams(window.location.search).get("focus") || null;
}

function updateUrl(slug, historyMode = "push") {
  const url = new URL(window.location.href);
  if (slug) {
    url.searchParams.set("focus", slug);
  } else {
    url.searchParams.delete("focus");
  }
  if (historyMode === "push") history.pushState({ focus: slug }, "", url);
  else if (historyMode === "replace") history.replaceState({ focus: slug }, "", url);
  // "none": popstate handler — URL is already correct, don't touch history
}

// --------------------------------------------------------------------------- //
// ARIA announcements                                                           //
// --------------------------------------------------------------------------- //

function announceMode(message) {
  const region = document.getElementById("tree-aria-live");
  if (!region) return;
  region.textContent = "";
  // Force a reflow so screen readers detect the change
  void region.offsetWidth;
  region.textContent = message;
}

// --------------------------------------------------------------------------- //
// Misc helpers                                                                 //
// --------------------------------------------------------------------------- //

function populateGroupFilter(nodes) {
  const gf = document.getElementById("group-filter");
  if (!gf || gf.dataset.populated) return;
  gf.dataset.populated = "1";
  const groups = [...new Set(nodes.map((n) => n.group).filter(Boolean))].sort();
  for (const g of groups) {
    const opt = document.createElement("option");
    opt.value = g; opt.textContent = g;
    gf.appendChild(opt);
  }
}

function showLoading(root, message) {
  root.innerHTML = `<div class="tree-loading" role="status">
    <div class="spinner" aria-label="Loading..."></div>
    <span>${escapeHtml(message)}</span>
  </div>`;
}

function showInvalidFocusMessage(root, slug) {
  const banner = document.createElement("div");
  banner.className = "tree-invalid-focus";
  banner.setAttribute("role", "alert");
  banner.textContent = `Module "${slug}" not found — showing full graph instead.`;
  root.parentElement?.insertBefore(banner, root);
  setTimeout(() => banner.remove(), 5000);
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
