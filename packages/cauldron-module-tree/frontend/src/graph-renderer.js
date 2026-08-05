/**
 * Renders the ELK layout result as positioned DOM nodes with an SVG edge overlay.
 * Returns an app object used by interaction.js.
 *
 * focusConfig (optional):
 *   roles         – { [slug]: "selected"|"dependency"|"parent_context" }
 *   displayEdges  – edges to render (may differ from ELK layout edges)
 */
export function renderGraph(container, elkLayout, graphData, { canChange, slugColor, focusConfig = null }) {
  const nodeMap = Object.fromEntries(graphData.nodes.map((n) => [n.slug, n]));
  const focusRoles = focusConfig?.roles ?? {};
  const displayEdges = focusConfig?.displayEdges ?? graphData.edges;

  // Canvas wrapper for pan/zoom
  const canvas = document.createElement("div");
  canvas.id = "tree-canvas";
  canvas.style.cssText = "position:relative;transform-origin:0 0;";

  // Compute total canvas size from ELK layout
  const elkNodes = elkLayout.children || [];
  let maxX = 0, maxY = 0;
  for (const en of elkNodes) {
    maxX = Math.max(maxX, en.x + en.width);
    maxY = Math.max(maxY, en.y + en.height);
  }
  const PAD = 40;
  canvas.style.width = `${maxX + PAD}px`;
  canvas.style.height = `${maxY + PAD}px`;

  // Render nodes
  const nodeEls = {};
  for (const en of elkNodes) {
    const node = nodeMap[en.id];
    if (!node) continue;
    const color = slugColor(node.slug);
    const role = focusRoles[node.slug] ?? null;
    const isParentCtx = role === "parent_context";
    const isSelected = role === "selected";

    const el = document.createElement("div");
    el.className = `module-node node-state-${node.state}${isParentCtx ? " is-parent-context" : ""}${isSelected ? " is-focus-selected" : ""}`;
    el.dataset.slug = node.slug;
    if (role) el.dataset.focusRole = role;
    el.style.cssText = `position:absolute;left:${en.x}px;top:${en.y}px;width:${en.width}px;`;
    el.style.setProperty("--module-color", color);

    const cardStyle = isSelected
      ? `border-top:3px solid ${escapeAttr(color)};border:2px solid ${escapeAttr(color)};`
      : `border-top:3px solid ${escapeAttr(color)};`;

    el.innerHTML = `<div class="node-card" style="${cardStyle}">
      <div class="node-icon" aria-hidden="true">${node.icon_svg || defaultIcon(node.slug, color)}</div>
      <div class="node-body">
        <div class="node-title">${escHtml(node.title || node.slug)}</div>
        <div class="node-slug">${escHtml(node.slug)}</div>
        <span class="state-badge badge-${node.state}">${node.state}</span>
        ${isSelected ? '<span class="focus-selected-badge">Selected</span>' : ""}
        ${isParentCtx ? '<span class="focus-parent-badge">Used by</span>' : ""}
      </div>
    </div>`;
    canvas.appendChild(el);
    nodeEls[node.slug] = { el, elkNode: en };
  }

  // Render SVG edges
  const svg = makeSvgEl("svg");
  svg.setAttribute("class", "edge-svg");
  svg.style.cssText = `position:absolute;top:0;left:0;pointer-events:none;overflow:visible;`;
  svg.setAttribute("width", maxX + PAD);
  svg.setAttribute("height", maxY + PAD);

  const defs = makeSvgEl("defs");
  // Arrowhead markers per edge kind
  for (const [kind, color] of [["required","#6366f1"],["optional","#9ca3af"],["capability","#8b5cf6"],["error","#ef4444"],["parent_context","#9ca3af"]]) {
    defs.appendChild(makeArrowMarker(kind, color));
  }
  svg.appendChild(defs);

  // Build a map from ELK edge id → ELK edge (for layout-edge lookup)
  const elkEdgeMap = {};
  for (const ee of elkLayout.edges || []) {
    elkEdgeMap[ee.id] = ee;
  }

  // Draw display edges. For parent_context edges there is no ELK layout edge
  // (ELK used a reversed layout edge); we draw them as straight lines between
  // node centers instead.
  for (let idx = 0; idx < displayEdges.length; idx++) {
    const edge = displayEdges[idx];

    if (edge.kind === "parent_context") {
      // Draw a muted straight/bent line from selected node up to parent node
      const srcEl = nodeEls[edge.source];
      const tgtEl = nodeEls[edge.target];
      if (!srcEl || !tgtEl) continue;

      const sx = srcEl.elkNode.x + srcEl.elkNode.width / 2;
      const sy = srcEl.elkNode.y;  // top of selected
      const tx = tgtEl.elkNode.x + tgtEl.elkNode.width / 2;
      const ty = tgtEl.elkNode.y + tgtEl.elkNode.height;  // bottom of parent

      const path = makeSvgEl("path");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#9ca3af");
      path.setAttribute("stroke-width", "1.5");
      path.setAttribute("stroke-dasharray", "5 3");
      path.setAttribute("marker-end", "url(#arrow-parent_context)");
      // Simple L-shaped path: go up to midpoint y, then across to target x
      const midY = (sy + ty) / 2;
      path.setAttribute("d", `M${sx},${sy} L${sx},${midY} L${tx},${midY} L${tx},${ty}`);
      path.dataset.source = edge.source;
      path.dataset.target = edge.target;
      path.dataset.kind = "parent_context";
      path.setAttribute("aria-label", `Used by ${edge.target}`);
      svg.appendChild(path);
      continue;
    }

    // Regular dependency edge: find the corresponding ELK layout edge
    // The layout edges are indexed in the order they appear in layoutEdges,
    // which may differ from displayEdges when focused. Use a stable id lookup.
    const ee = elkEdgeMap[`edge_${idx}`];
    if (!ee) continue;

    const sourceColor = slugColor(edge.source);
    const isError = edge.status === "conflict" || edge.status === "blocked" || edge.status === "cycle";
    const strokeColor = isError ? "#ef4444" : sourceColor;
    const dashArray = edge.kind === "optional" ? "6 3" : edge.kind === "capability" ? "2 3" : "none";
    const markerKind = isError ? "error" : edge.kind;

    const path = makeSvgEl("path");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", strokeColor);
    path.setAttribute("stroke-width", "2");
    if (dashArray !== "none") path.setAttribute("stroke-dasharray", dashArray);
    path.setAttribute("marker-end", `url(#arrow-${markerKind})`);
    path.setAttribute("d", buildElkPath(ee, elkLayout));
    path.dataset.source = edge.source;
    path.dataset.target = edge.target;
    path.dataset.kind = edge.kind;
    svg.appendChild(path);
  }

  canvas.appendChild(svg);
  container.appendChild(canvas);

  return {
    canvas,
    nodeEls,
    graphData,
    elkLayout,
    slugColor,
    canChange,
  };
}

function buildElkPath(elkEdge, layout) {
  // ELK provides sections[] with startPoint, endPoint, bendPoints[]
  const sections = elkEdge.sections || [];
  if (!sections.length) return "";
  const parts = [];
  for (const sec of sections) {
    const { startPoint: s, endPoint: e, bendPoints: bends = [] } = sec;
    parts.push(`M${s.x},${s.y}`);
    for (const b of bends) parts.push(`L${b.x},${b.y}`);
    parts.push(`L${e.x},${e.y}`);
  }
  return parts.join(" ");
}

function makeArrowMarker(id, color) {
  const marker = makeSvgEl("marker");
  marker.id = `arrow-${id}`;
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const path = makeSvgEl("path");
  path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  path.setAttribute("fill", color);
  marker.appendChild(path);
  return marker;
}

function makeSvgEl(tag) {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

function defaultIcon(slug, color) {
  const letter = (slug[0] || "?").toUpperCase();
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><circle cx="20" cy="20" r="18" fill="${escapeAttr(color)}"/><text x="20" y="26" text-anchor="middle" font-size="18" fill="white" font-family="system-ui">${letter}</text></svg>`;
}

function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/"/g, "&quot;");
}
