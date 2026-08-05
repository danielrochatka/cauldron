/**
 * Interaction layer: pan, zoom, node selection, focus mode, search,
 * state/group filters, enable/disable workflow.
 */
export function initInteraction(app, container, graphData, { canChange }) {
  const { canvas, nodeEls, slugColor } = app;
  let scale = 1, panX = 0, panY = 0;
  let selectedSlug = null;
  let focusMode = false;

  // Build adjacency for focus mode
  const adj = {}, revAdj = {};
  for (const e of graphData.edges) {
    (adj[e.source] = adj[e.source] || new Set()).add(e.target);
    (revAdj[e.target] = revAdj[e.target] || new Set()).add(e.source);
  }

  function ancestors(slug, visited = new Set()) {
    if (visited.has(slug)) return visited;
    visited.add(slug);
    for (const p of revAdj[slug] || []) ancestors(p, visited);
    return visited;
  }
  function descendants(slug, visited = new Set()) {
    if (visited.has(slug)) return visited;
    visited.add(slug);
    for (const c of adj[slug] || []) descendants(c, visited);
    return visited;
  }

  // Pan & zoom
  let isPanning = false, panStart = null, panOrigin = null;
  container.addEventListener("mousedown", (e) => {
    if (e.target.closest(".module-node")) return;
    isPanning = true;
    panStart = { x: e.clientX, y: e.clientY };
    panOrigin = { x: panX, y: panY };
    container.classList.add("is-panning");
  });
  window.addEventListener("mousemove", (e) => {
    if (!isPanning) return;
    panX = panOrigin.x + e.clientX - panStart.x;
    panY = panOrigin.y + e.clientY - panStart.y;
    applyTransform();
  });
  window.addEventListener("mouseup", () => {
    isPanning = false;
    container.classList.remove("is-panning");
  });
  container.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    scale = Math.max(0.2, Math.min(3, scale * factor));
    applyTransform();
  }, { passive: false });

  function applyTransform() {
    canvas.style.transform = `translate(${panX}px,${panY}px) scale(${scale})`;
  }

  // Fit to view
  document.getElementById("btn-fit")?.addEventListener("click", fitToView);
  function fitToView() {
    const cr = container.getBoundingClientRect();
    const cw = parseFloat(canvas.style.width) || 800;
    const ch = parseFloat(canvas.style.height) || 600;
    scale = Math.min(1, (cr.width - 40) / cw, (cr.height - 40) / ch);
    panX = (cr.width - cw * scale) / 2;
    panY = 20;
    applyTransform();
  }
  setTimeout(fitToView, 50);

  // Reset view
  document.getElementById("btn-reset-layout")?.addEventListener("click", () => {
    scale = 1; panX = 0; panY = 0; applyTransform();
  });

  // Node selection & focus mode
  function selectNode(slug) {
    selectedSlug = slug;
    const anc = ancestors(slug, new Set());
    const desc = descendants(slug, new Set());
    anc.delete(slug); desc.delete(slug);
    const related = new Set([slug, ...anc, ...desc]);

    // Highlight nodes
    for (const [s, { el }] of Object.entries(nodeEls)) {
      el.classList.toggle("is-selected", s === slug);
      el.classList.toggle("is-ancestor", anc.has(s));
      el.classList.toggle("is-descendant", desc.has(s));
      el.classList.toggle("is-dimmed", !related.has(s));
    }

    // Highlight edges
    for (const path of canvas.querySelectorAll(".edge-svg path")) {
      const src = path.dataset.source, tgt = path.dataset.target;
      path.classList.toggle("is-highlighted", related.has(src) && related.has(tgt));
      path.classList.toggle("is-dimmed", !(related.has(src) && related.has(tgt)));
      path.style.strokeWidth = (src === slug || tgt === slug) ? "3" : "2";
    }

    showDetailPanel(slug, anc, desc);
  }

  function clearSelection() {
    selectedSlug = null;
    for (const { el } of Object.values(nodeEls)) {
      el.classList.remove("is-selected", "is-ancestor", "is-descendant", "is-dimmed");
    }
    for (const path of canvas.querySelectorAll(".edge-svg path")) {
      path.classList.remove("is-highlighted", "is-dimmed");
      path.style.strokeWidth = "2";
    }
    hideDetailPanel();
  }

  for (const [slug, { el }] of Object.entries(nodeEls)) {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      if (selectedSlug === slug) clearSelection();
      else selectNode(slug);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectNode(slug); }
    });
    el.setAttribute("tabindex", "0");
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", `Module: ${slug}`);
  }
  container.addEventListener("click", () => clearSelection());

  // Show all / focus
  document.getElementById("btn-show-all")?.addEventListener("click", clearSelection);
  document.getElementById("btn-focus")?.addEventListener("click", () => {
    if (selectedSlug) toggleFocusMode(selectedSlug);
  });

  function toggleFocusMode(slug) {
    focusMode = !focusMode;
    const anc = ancestors(slug, new Set());
    const desc = descendants(slug, new Set());
    const related = new Set([slug, ...anc, ...desc]);
    for (const [s, { el }] of Object.entries(nodeEls)) {
      el.style.display = focusMode && !related.has(s) ? "none" : "";
    }
    for (const path of canvas.querySelectorAll(".edge-svg path")) {
      const inFocus = related.has(path.dataset.source) && related.has(path.dataset.target);
      path.style.display = focusMode && !inFocus ? "none" : "";
    }
    document.getElementById("btn-focus")?.setAttribute("aria-pressed", String(focusMode));
  }

  // Search
  const searchInput = document.getElementById("tree-search");
  searchInput?.addEventListener("input", applyFilters);

  // State / group filters
  document.getElementById("state-filter")?.addEventListener("change", applyFilters);
  document.getElementById("group-filter")?.addEventListener("change", applyFilters);

  // Populate group filter
  const groups = [...new Set(graphData.nodes.map((n) => n.group).filter(Boolean))].sort();
  const gf = document.getElementById("group-filter");
  if (gf) {
    for (const g of groups) {
      const opt = document.createElement("option");
      opt.value = g; opt.textContent = g;
      gf.appendChild(opt);
    }
  }

  function applyFilters() {
    const q = (searchInput?.value || "").toLowerCase();
    const st = document.getElementById("state-filter")?.value || "";
    const gr = document.getElementById("group-filter")?.value || "";
    for (const [slug, { el }] of Object.entries(nodeEls)) {
      const n = graphData.nodes.find((x) => x.slug === slug);
      if (!n) continue;
      const match =
        (!q || n.slug.toLowerCase().includes(q) || (n.title || "").toLowerCase().includes(q)) &&
        (!st || n.state === st) &&
        (!gr || n.group === gr);
      el.style.display = match ? "" : "none";
    }
  }

  // Graph/table toggle
  document.getElementById("btn-view-graph")?.addEventListener("click", () => {
    container.style.display = "";
    document.getElementById("tree-table").style.display = "none";
    populateTable();
  });
  document.getElementById("btn-view-table")?.addEventListener("click", () => {
    container.style.display = "none";
    const tt = document.getElementById("tree-table");
    tt.style.display = "";
    populateTable();
  });

  function populateTable() {
    const tbody = document.getElementById("tree-table-body");
    if (!tbody || tbody.dataset.populated) return;
    tbody.dataset.populated = "1";
    for (const n of [...graphData.nodes].sort((a, b) => a.slug.localeCompare(b.slug))) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escHtml(n.slug)}</td><td><span class="state-badge badge-${n.state}">${n.state}</span></td><td>${escHtml(n.group||"")}</td><td>${escHtml(n.version||"")}</td><td>${(n.deps||[]).map(escHtml).join(", ")}</td>`;
      tbody.appendChild(tr);
    }
  }

  // Detail panel
  function showDetailPanel(slug, anc, desc) {
    const n = graphData.nodes.find((x) => x.slug === slug);
    if (!n) return;
    const panel = document.getElementById("detail-panel");
    if (!panel) return;
    panel.removeAttribute("aria-hidden");
    panel.innerHTML = `
      <div class="detail-header">
        <div class="detail-icon">${n.icon_svg || ""}</div>
        <div>
          <div class="detail-title">${escHtml(n.title || n.slug)}</div>
          <div class="detail-slug">${escHtml(n.slug)}</div>
        </div>
        <button class="detail-close" onclick="this.closest('#detail-panel').setAttribute('aria-hidden','true')" aria-label="Close">&times;</button>
      </div>
      <div class="detail-body">
        <p>${escHtml(n.summary || "")}</p>
        <dl>
          <dt>State</dt><dd><span class="state-badge badge-${n.state}">${n.state}</span></dd>
          <dt>Version</dt><dd>${escHtml(n.version || "—")}</dd>
          <dt>Group</dt><dd>${escHtml(n.group || "—")}</dd>
          <dt>Source</dt><dd>${escHtml(n.source || "—")}</dd>
          <dt>Ancestors</dt><dd>${anc.size} direct / transitive</dd>
          <dt>Descendants</dt><dd>${desc.size} direct / transitive</dd>
        </dl>
        ${n.errors?.length ? `<div class="detail-errors"><strong>Errors:</strong><ul>${n.errors.map((e) => `<li>${escHtml(e.message || JSON.stringify(e))}</li>`).join("")}</ul></div>` : ""}
        ${canChange ? renderActions(n) : ""}
      </div>`;
  }

  function renderActions(n) {
    const actionLabel = n.enabled ? "Disable" : "Enable";
    const previewUrl = container.dataset.previewUrl?.replace("__slug__", n.slug);
    return `<div class="detail-actions">
      <button class="cui-btn cui-btn-outline" data-action="${n.enabled ? "disable" : "enable"}" data-slug="${escapeAttr(n.slug)}" data-preview-url="${escapeAttr(previewUrl || "")}">
        ${actionLabel}
      </button>
      ${n.requires_restart ? '<span class="restart-warning">⚠ Requires restart</span>' : ""}
    </div>`;
  }

  function hideDetailPanel() {
    document.getElementById("detail-panel")?.setAttribute("aria-hidden", "true");
  }

  // Enable/disable actions (delegated)
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action][data-slug]");
    if (!btn) return;
    const action = btn.dataset.action;
    const slug = btn.dataset.slug;
    const previewUrl = btn.dataset.previewUrl;
    if (!previewUrl) return;
    e.preventDefault();

    btn.disabled = true;
    btn.textContent = "Previewing…";
    try {
      const r = await fetch(previewUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ action }),
        credentials: "same-origin",
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Preview failed");
      showConfirmModal(slug, action, data, container);
    } catch (err) {
      alert(`Error: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = action === "disable" ? "Disable" : "Enable";
    }
  });

  function showConfirmModal(slug, action, preview, container) {
    const modal = document.getElementById("confirm-modal") || createConfirmModal();
    const affected = preview.affected_modules || [];
    modal.innerHTML = `<div class="confirm-dialog">
      <h3>${action === "disable" ? "Disable" : "Enable"} ${escHtml(slug)}?</h3>
      ${affected.length ? `<p>This will also affect:</p><ul>${affected.map((s) => `<li>${escHtml(s)}</li>`).join("")}</ul>` : ""}
      ${preview.warnings?.length ? `<p class="warn">${preview.warnings.map(escHtml).join("<br>")}</p>` : ""}
      <div class="confirm-actions">
        <button id="confirm-cancel" class="cui-btn cui-btn-outline">Cancel</button>
        <button id="confirm-ok" class="cui-btn cui-btn-primary">Confirm</button>
      </div>
    </div>`;
    modal.classList.add("is-open");
    document.getElementById("confirm-cancel")?.addEventListener("click", () => modal.classList.remove("is-open"));
    document.getElementById("confirm-ok")?.addEventListener("click", async () => {
      modal.classList.remove("is-open");
      const url = action === "disable"
        ? container.dataset.disableUrl?.replace("__slug__", slug)
        : container.dataset.enableUrl?.replace("__slug__", slug);
      if (!url) return;
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ reason: "" }),
        credentials: "same-origin",
      });
      if (r.ok) location.reload();
      else alert("Action failed: " + r.status);
    });
  }

  function createConfirmModal() {
    const m = document.createElement("div");
    m.id = "confirm-modal";
    document.body.appendChild(m);
    return m;
  }

  function getCsrf() {
    return document.cookie.split(";").find((c) => c.trim().startsWith("csrftoken="))?.split("=")[1] || "";
  }

  function escHtml(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function escapeAttr(s) {
    return String(s ?? "").replace(/"/g, "&quot;");
  }
}
