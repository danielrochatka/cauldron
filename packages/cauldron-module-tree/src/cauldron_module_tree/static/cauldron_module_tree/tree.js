/**
 * cauldron_module_tree/tree.js — ES module, no external dependencies.
 * Fetches the graph API, computes a BFS layered layout, renders nodes as
 * positioned divs with an SVG edge overlay, and handles search, filtering,
 * detail panel, pan/zoom, and enable/disable actions.
 */

const NODE_W = 190, NODE_H = 80, GAP_X = 30, GAP_Y = 130, PAD = 40;
const EDGE_COLORS = { required: "#3b82f6", optional: "#9ca3af", capability: "#8b5cf6" };

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("tree-root");
  if (root) new ModuleTreeApp(root).init();
});

class ModuleTreeApp {
  constructor(root) {
    this.root      = root;
    this.graphUrl  = root.dataset.graphUrl;
    this.previewUrl = root.dataset.previewUrl;
    this.enableUrl  = root.dataset.enableUrl;
    this.disableUrl = root.dataset.disableUrl;
    this.canChange  = root.dataset.canChange === "1";
    this.graphData  = null;
    this.positions  = {};
    this.nodeEls    = {};
    this.selected   = null;
    this.scale = 1; this.pan = { x: 0, y: 0 };
    this.filterState = ""; this.filterGroup = ""; this.searchQuery = "";
    this._panStart = null; this._panOrigin = null;
  }

  // ── Init ────────────────────────────────────────────────────────────────────

  async init() {
    this._bindToolbar();
    this._bindDetailPanel();
    this._bindModal();

    try {
      const r = await fetch(this.graphUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      this.graphData = await r.json();
    } catch (e) { return this._showState("error", e.message); }

    if (!this.graphData.nodes?.length) return this._showState("empty");

    this._populateGroupFilter();
    this._buildTable();
    requestAnimationFrame(() => {
      this._layout();
      this._render();
      this._bindPanZoom();
      new ResizeObserver(() => this._redrawEdges()).observe(this.root);
    });
  }

  // ── Layout ─────────────────────────────────────────────────────────────────

  _layout() {
    const nodes = this.graphData.nodes, edges = this.graphData.edges;
    const children = {}, inDeg = {};
    for (const n of nodes) { children[n.slug] = []; inDeg[n.slug] = new Set(); }
    for (const e of edges) {
      if (children[e.source] && inDeg[e.target]) {
        children[e.source].push(e.target); inDeg[e.target].add(e.source);
      }
    }

    const depth = {}, visited = new Set();
    const roots = nodes.filter(n => inDeg[n.slug].size === 0).map(n => n.slug);
    const queue = [...roots];
    for (const r of roots) depth[r] = 0;

    while (queue.length) {
      const slug = queue.shift();
      if (visited.has(slug)) continue;
      visited.add(slug);
      for (const c of children[slug]) {
        if (!visited.has(c)) { depth[c] = Math.max(depth[c] ?? 0, (depth[slug] ?? 0) + 1); queue.push(c); }
      }
    }
    for (const n of nodes) if (depth[n.slug] === undefined) depth[n.slug] = 0;

    const layers = {};
    for (const n of nodes) { const d = depth[n.slug]; (layers[d] = layers[d] || []).push(n.slug); }

    this.positions = {};
    for (const [d, slugs] of Object.entries(layers)) {
      slugs.forEach((slug, i) => {
        this.positions[slug] = { x: PAD + i * (NODE_W + GAP_X), y: PAD + Number(d) * (NODE_H + GAP_Y) };
      });
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  _render() {
    document.getElementById("tree-loading")?.remove();
    let canvas = document.getElementById("tree-canvas");
    if (!canvas) { canvas = document.createElement("div"); canvas.id = "tree-canvas"; this.root.appendChild(canvas); }
    canvas.innerHTML = ""; this.nodeEls = {};

    let maxX = 0, maxY = 0;
    for (const p of Object.values(this.positions)) { maxX = Math.max(maxX, p.x + NODE_W); maxY = Math.max(maxY, p.y + NODE_H); }
    canvas.style.cssText = `position:relative;width:${maxX + PAD}px;height:${maxY + PAD}px`;

    for (const node of this.graphData.nodes) {
      const pos = this.positions[node.slug]; if (!pos) continue;
      const el = this._makeNode(node);
      el.style.left = pos.x + "px"; el.style.top = pos.y + "px";
      canvas.appendChild(el); this.nodeEls[node.slug] = el;
    }
    this._drawEdges(canvas, maxX + PAD, maxY + PAD);
    this._applyFilters();
  }

  _makeNode(node) {
    const el = document.createElement("div");
    el.className = `module-node node-state-${node.state}`;
    el.dataset.slug = node.slug;
    el.style.cssText = `position:absolute;width:${NODE_W}px`;
    el.innerHTML = `<div class="node-card">
      <div class="node-icon">${node.icon_svg || _defaultIcon(node.slug)}</div>
      <div class="node-body">
        <div class="node-title" title="${_e(node.title)}">${_e(node.title)}</div>
        <div class="node-slug">${_e(node.slug)}</div>
        <div class="node-meta"><span class="state-badge badge-${node.state}">${node.state}</span></div>
      </div></div>`;
    el.addEventListener("click", e => { e.stopPropagation(); this._select(node.slug); });
    return el;
  }

  _drawEdges(canvas, w, h) {
    canvas.querySelector(".edge-svg")?.remove();
    const svg = _svgEl("svg"); svg.setAttribute("class", "edge-svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.style.cssText = "position:absolute;top:0;left:0;pointer-events:none;overflow:visible";

    const defs = _svgEl("defs");
    for (const [k, c] of Object.entries(EDGE_COLORS)) defs.appendChild(_marker(k, c));
    svg.appendChild(defs);

    for (const e of this.graphData.edges) {
      const s = this.positions[e.source], t = this.positions[e.target]; if (!s || !t) continue;
      const x1 = s.x + NODE_W / 2, y1 = s.y + NODE_H, x2 = t.x + NODE_W / 2, y2 = t.y, my = (y1 + y2) / 2;
      const p = _svgEl("path");
      p.setAttribute("d", `M${x1},${y1} L${x1},${my} L${x2},${my} L${x2},${y2}`);
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", EDGE_COLORS[e.kind] || EDGE_COLORS.required);
      p.setAttribute("stroke-width", "1.5");
      p.setAttribute("marker-end", `url(#arrow-${e.kind})`);
      if (e.status !== "resolved") p.setAttribute("stroke-dasharray", "4 3");
      svg.appendChild(p);
    }
    canvas.insertBefore(svg, canvas.firstChild);
  }

  _redrawEdges() {
    const canvas = document.getElementById("tree-canvas"); if (!canvas) return;
    this._drawEdges(canvas, parseInt(canvas.style.width) || 800, parseInt(canvas.style.height) || 600);
  }

  // ── Selection & detail ─────────────────────────────────────────────────────

  _select(slug) {
    if (this.selected) this.nodeEls[this.selected]?.classList.remove("is-selected");
    this.selected = slug;
    this.nodeEls[slug]?.classList.add("is-selected");
    const node = this.graphData.nodes.find(n => n.slug === slug);
    if (node) this._openPanel(node);
  }

  _openPanel(node) {
    const panel = document.getElementById("detail-panel");
    document.getElementById("detail-icon").innerHTML = node.icon_svg || _defaultIcon(node.slug);
    document.getElementById("detail-title-text").textContent = node.title;
    document.getElementById("detail-slug-text").textContent  = node.slug;

    const body = document.getElementById("detail-body");
    const s = (label, html) => `<div class="detail-section"><div class="detail-section-label">${_e(label)}</div><div class="detail-section-value">${html}</div></div>`;
    const chips = (arr, clickable) => `<div class="detail-chip-list">${arr.map(v => clickable ? `<button class="detail-chip" data-select="${_e(v)}">${_e(v)}</button>` : `<span class="detail-chip">${_e(v)}</span>`).join("")}</div>`;

    let html = s("State", `<span class="state-badge badge-${node.state}">${node.state}</span>`);
    if (node.version)          html += s("Version",  _e(node.version));
    if (node.summary)          html += s("Summary",  _e(node.summary));
    if (node.source_type || node.source) html += s("Source", `<code>${_e([node.source_type, node.source].filter(Boolean).join(" — "))}</code>`);
    if (node.group)            html += s("Group",    _e(node.group));
    if (node.children?.length) html += s("Dependencies", chips(node.children, true));
    if (node.parents?.length)  html += s("Dependents",   chips(node.parents,  true));
    if (node.provides?.length) html += s("Provides",     chips(node.provides, false));
    if (node.errors?.length)   html += s("Errors", node.errors.map(e => `<div class="error-item">${_e(String(e))}</div>`).join(""));
    if (node.documentation_url) html += s("Documentation", `<a href="${_e(node.documentation_url)}" target="_blank" rel="noopener noreferrer">${_e(node.documentation_url)}</a>`);
    body.innerHTML = html;

    body.querySelectorAll("[data-select]").forEach(b => b.addEventListener("click", () => this._select(b.dataset.select)));

    const actEl = document.getElementById("detail-actions");
    actEl.innerHTML = ""; actEl.style.display = this.canChange ? "flex" : "none";
    if (this.canChange) {
      const isDisabled = node.state === "disabled";
      const btn = document.createElement("button");
      btn.className = isDisabled ? "cui-btn cui-btn-primary" : "cui-btn cui-btn-danger";
      btn.textContent = isDisabled ? "Enable" : "Disable";
      btn.addEventListener("click", () => this._doAction(node, isDisabled ? "enable" : "disable"));
      actEl.appendChild(btn);
    }

    panel.classList.add("is-open"); panel.setAttribute("aria-hidden", "false");
  }

  _closePanel() {
    document.getElementById("detail-panel").classList.remove("is-open");
    document.getElementById("detail-panel").setAttribute("aria-hidden", "true");
    if (this.selected) { this.nodeEls[this.selected]?.classList.remove("is-selected"); this.selected = null; }
  }

  // ── Enable/Disable ─────────────────────────────────────────────────────────

  async _doAction(node, action) {
    let preview;
    try {
      const r = await fetch(this.previewUrl.replace("__slug__", node.slug), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() },
        body: JSON.stringify({ action }),
      });
      preview = await r.json();
    } catch (e) { return alert(`Preview failed: ${e.message}`); }

    let bHtml = `<p>You are about to <strong>${_e(action)}</strong> <code>${_e(node.slug)}</code>.</p>`;
    if (preview.warnings?.length)         bHtml += `<ul>${preview.warnings.map(w => `<li>${_e(w)}</li>`).join("")}</ul>`;
    if (preview.affected_modules?.length) bHtml += `<p>Affected: ${preview.affected_modules.map(s => `<code>${_e(s)}</code>`).join(", ")}</p>`;
    if (preview.restart_required)         bHtml += `<p><strong>Server restart required.</strong></p>`;
    if (!preview.allowed)                 bHtml += `<p style="color:#991b1b">${_e((preview.validation_errors || []).join(" "))}</p>`;

    this._confirm(`${action[0].toUpperCase() + action.slice(1)} module`, bHtml, preview.allowed, async () => {
      const url = (action === "enable" ? this.enableUrl : this.disableUrl).replace("__slug__", node.slug);
      try {
        const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() }, body: JSON.stringify({}) });
        const res = await r.json();
        if (r.ok) { alert(res.message || "Done."); location.reload(); }
        else alert(`Error: ${res.error || r.statusText}`);
      } catch (e) { alert(`Request failed: ${e.message}`); }
    });
  }

  // ── Filters ────────────────────────────────────────────────────────────────

  _applyFilters() {
    const q = this.searchQuery.trim().toLowerCase();
    for (const node of this.graphData.nodes) {
      const el = this.nodeEls[node.slug]; if (!el) continue;
      const ok = (!q || node.slug.toLowerCase().includes(q) || node.title.toLowerCase().includes(q))
               && (!this.filterState || node.state === this.filterState)
               && (!this.filterGroup || node.group === this.filterGroup);
      el.classList.toggle("is-dimmed", !ok);
    }
  }

  _populateGroupFilter() {
    const sel = document.getElementById("group-filter"); if (!sel) return;
    [...new Set(this.graphData.nodes.map(n => n.group).filter(Boolean))].sort()
      .forEach(g => { const o = document.createElement("option"); o.value = o.textContent = g; sel.appendChild(o); });
  }

  _buildTable() {
    const tbody = document.getElementById("tree-table-body"); if (!tbody) return;
    for (const n of this.graphData.nodes) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td><strong>${_e(n.title)}</strong><br><code style="font-size:11px">${_e(n.slug)}</code></td>
        <td><span class="state-badge badge-${n.state}">${n.state}</span></td>
        <td>${_e(n.group || "—")}</td><td>${_e(n.version || "—")}</td>
        <td style="font-size:12px;font-family:monospace">${(n.children || []).map(s => _e(s)).join(", ") || "—"}</td>`;
      tr.style.cursor = "pointer";
      tr.addEventListener("click", () => this._select(n.slug));
      tbody.appendChild(tr);
    }
  }

  // ── Fit / Reset ────────────────────────────────────────────────────────────

  _fitToView() {
    const c = document.getElementById("tree-canvas"); if (!c) return;
    const rW = this.root.clientWidth, rH = this.root.clientHeight;
    const cW = parseInt(c.style.width) || 800, cH = parseInt(c.style.height) || 600;
    this.scale = Math.min((rW - PAD * 2) / cW, (rH - PAD * 2) / cH, 1);
    this.pan = { x: (rW - cW * this.scale) / 2, y: (rH - cH * this.scale) / 2 };
    this._applyXform();
  }

  _resetLayout() { this.scale = 1; this.pan = { x: 0, y: 0 }; this._applyXform(); }

  _applyXform() {
    const c = document.getElementById("tree-canvas");
    if (c) c.style.transform = `translate(${this.pan.x}px,${this.pan.y}px) scale(${this.scale})`;
  }

  // ── Pan / Zoom ─────────────────────────────────────────────────────────────

  _bindPanZoom() {
    const isCanvas = el => el === this.root || el?.id === "tree-canvas";
    this.root.addEventListener("mousedown", e => {
      if (!isCanvas(e.target)) return;
      this._panStart = { x: e.clientX, y: e.clientY }; this._panOrigin = { ...this.pan };
      this.root.classList.add("is-panning");
    });
    window.addEventListener("mousemove", e => {
      if (!this._panStart) return;
      this.pan = { x: this._panOrigin.x + e.clientX - this._panStart.x, y: this._panOrigin.y + e.clientY - this._panStart.y };
      this._applyXform();
    });
    window.addEventListener("mouseup", () => { this._panStart = null; this.root.classList.remove("is-panning"); });
    this.root.addEventListener("wheel", e => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1, ns = Math.min(Math.max(this.scale * delta, 0.2), 3);
      const rect = this.root.getBoundingClientRect(), cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      this.pan.x = cx - (cx - this.pan.x) * (ns / this.scale);
      this.pan.y = cy - (cy - this.pan.y) * (ns / this.scale);
      this.scale = ns; this._applyXform();
    }, { passive: false });
    this.root.addEventListener("click", e => { if (isCanvas(e.target)) this._closePanel(); });
  }

  // ── Toolbar ────────────────────────────────────────────────────────────────

  _bindToolbar() {
    _on("tree-search",      "input",  e => { this.searchQuery = e.target.value; this._applyFilters(); });
    _on("state-filter",     "change", e => { this.filterState = e.target.value; this._applyFilters(); });
    _on("group-filter",     "change", e => { this.filterGroup = e.target.value; this._applyFilters(); });
    _on("btn-fit",          "click",  () => this._fitToView());
    _on("btn-reset-layout", "click",  () => this._resetLayout());

    _on("btn-view-graph", "click", () => {
      document.getElementById("tree-root").style.display  = "";
      document.getElementById("tree-table").style.display = "none";
      document.getElementById("tree-table").setAttribute("aria-hidden", "true");
      document.getElementById("btn-view-graph").classList.add("is-active");
      document.getElementById("btn-view-table").classList.remove("is-active");
    });
    _on("btn-view-table", "click", () => {
      document.getElementById("tree-root").style.display  = "none";
      document.getElementById("tree-table").style.display = "";
      document.getElementById("tree-table").setAttribute("aria-hidden", "false");
      document.getElementById("btn-view-table").classList.add("is-active");
      document.getElementById("btn-view-graph").classList.remove("is-active");
    });
  }

  _bindDetailPanel() {
    _on("detail-close", "click", () => this._closePanel());
    document.addEventListener("keydown", e => { if (e.key === "Escape") this._closePanel(); });
  }

  // ── Confirm modal ──────────────────────────────────────────────────────────

  _bindModal() {
    const modal = document.getElementById("confirm-modal");
    _on("confirm-cancel", "click", () => modal?.classList.remove("is-open"));
    modal?.addEventListener("click", e => { if (e.target === modal) modal.classList.remove("is-open"); });
  }

  _confirm(title, bodyHtml, allowed, onOk) {
    const modal = document.getElementById("confirm-modal");
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-body").innerHTML    = bodyHtml;
    const oldOk = document.getElementById("confirm-ok");
    const newOk = oldOk.cloneNode(true); oldOk.replaceWith(newOk);
    newOk.disabled = !allowed;
    newOk.addEventListener("click", async () => { modal.classList.remove("is-open"); await onOk(); });
    modal.classList.add("is-open");
  }

  // ── Loading / error / empty ────────────────────────────────────────────────

  _showState(type, msg) {
    const el = document.getElementById("tree-loading"); if (!el) return;
    const icons = {
      error: `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
      empty: `<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/></svg>`,
    };
    const labels = { error: "Failed to load module graph", empty: "No modules found" };
    el.innerHTML = `${icons[type]}<strong>${labels[type]}</strong>${msg ? `<span>${_e(msg)}</span>` : ""}`;
    el.className = `tree-${type}`;
  }
}

// ─── Utilities ───────────────────────────────────────────────────────────────

function _e(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _on(id, evt, fn) {
  document.getElementById(id)?.addEventListener(evt, fn);
}

function _svgEl(tag) {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

function _marker(id, color) {
  const m = _svgEl("marker");
  m.setAttribute("id", `arrow-${id}`);
  m.setAttribute("viewBox", "0 0 8 8"); m.setAttribute("refX", "6"); m.setAttribute("refY", "4");
  m.setAttribute("markerWidth", "6"); m.setAttribute("markerHeight", "6"); m.setAttribute("orient", "auto-start-reverse");
  const p = _svgEl("path"); p.setAttribute("d", "M0,0 L8,4 L0,8 Z"); p.setAttribute("fill", color);
  m.appendChild(p); return m;
}

function _defaultIcon(slug) {
  const i = (slug || "?").split(".").pop().slice(0, 2).toUpperCase();
  return `<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect width="32" height="32" rx="4" fill="#e0e7ff"/><text x="16" y="21" text-anchor="middle" font-size="13" font-family="sans-serif" fill="#4338ca" font-weight="600">${i}</text></svg>`;
}

function _csrf() {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/); return m ? m[1] : "";
}
