/**
 * Renders the ELK layout result as positioned DOM nodes with an SVG edge overlay.
 * Returns an app object used by interaction.js.
 */
export function renderGraph(container, elkLayout, graphData, { canChange, slugColor }) {
  const nodeMap = Object.fromEntries(graphData.nodes.map((n) => [n.slug, n]));

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
    const el = document.createElement("div");
    el.className = `module-node node-state-${node.state}`;
    el.dataset.slug = node.slug;
    el.style.cssText = `position:absolute;left:${en.x}px;top:${en.y}px;width:${en.width}px;`;
    el.style.setProperty("--module-color", color);
    el.innerHTML = `<div class="node-card" style="border-top:3px solid ${escapeAttr(color)}">
      <div class="node-icon" aria-hidden="true">${node.icon_svg || defaultIcon(node.slug, color)}</div>
      <div class="node-body">
        <div class="node-title">${escHtml(node.title || node.slug)}</div>
        <div class="node-slug">${escHtml(node.slug)}</div>
        <span class="state-badge badge-${node.state}">${node.state}</span>
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
  for (const [kind, color] of [["required","#6366f1"],["optional","#9ca3af"],["capability","#8b5cf6"],["error","#ef4444"]]) {
    defs.appendChild(makeArrowMarker(kind, color));
  }
  svg.appendChild(defs);

  // Draw edges using ELK's computed bend points (orthogonal routing)
  const elkEdgeMap = {};
  for (const ee of elkLayout.edges || []) {
    elkEdgeMap[ee.id] = ee;
  }

  for (let idx = 0; idx < graphData.edges.length; idx++) {
    const edge = graphData.edges[idx];
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
