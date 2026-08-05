/**
 * Vitest tests for the module-tree frontend.
 *
 * Tests are split into two groups:
 *   1. Pure HTML builders from state.js  (no DOM needed)
 *   2. DOM behaviour via initInteraction  (jsdom environment)
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { buildActionHtml, buildStateRows, pendingWarning } from "./state.js";
import { initInteraction } from "./interaction.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeNode(overrides = {}) {
  return {
    slug: "test.mod",
    title: "Test Module",
    summary: "",
    version: "1.0.0",
    state: "ready",
    enabled: true,
    active: true,
    configured_enabled: true,
    pending_restart: false,
    requires_restart: false,
    icon_svg: "",
    visual_color: "#6366f1",
    group: "",
    display_order: 0,
    errors: [],
    parents: [],
    children: [],
    ...overrides,
  };
}

function parseHtml(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

// ---------------------------------------------------------------------------
// 1. Pure state-html tests (no DOM)
// ---------------------------------------------------------------------------

describe("buildActionHtml", () => {
  it("no pending + enabled → Disable button", () => {
    const n = makeNode({ configured_enabled: true, pending_restart: false });
    const html = buildActionHtml(n, "/preview/");
    const el = parseHtml(html);
    const btn = el.querySelector("button[data-action='disable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Disable");
  });

  it("no pending + disabled → Enable button", () => {
    const n = makeNode({ enabled: false, active: false, configured_enabled: false, pending_restart: false });
    const html = buildActionHtml(n, "/preview/");
    const el = parseHtml(html);
    const btn = el.querySelector("button[data-action='enable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Enable");
  });

  it("pending disable → Undo pending disable", () => {
    const n = makeNode({ enabled: true, configured_enabled: false, pending_restart: true });
    const html = buildActionHtml(n, "/preview/");
    const el = parseHtml(html);
    const btn = el.querySelector("button[data-action='enable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Undo pending disable");
  });

  it("pending enable → Undo pending enable", () => {
    const n = makeNode({ enabled: false, active: false, configured_enabled: true, pending_restart: true });
    const html = buildActionHtml(n, "/preview/");
    const el = parseHtml(html);
    const btn = el.querySelector("button[data-action='disable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Undo pending enable");
  });
});

describe("pendingWarning", () => {
  it("no pending → empty string", () => {
    expect(pendingWarning(makeNode({ pending_restart: false }))).toBe("");
  });

  it("pending disable → correct label", () => {
    const w = pendingWarning(makeNode({ configured_enabled: false, pending_restart: true }));
    expect(w).toBe("Pending disable — restart required");
  });

  it("pending enable → correct label", () => {
    const w = pendingWarning(makeNode({ configured_enabled: true, pending_restart: true }));
    expect(w).toBe("Pending enable — restart required");
  });
});

describe("buildStateRows", () => {
  it("enabled + active → correct labels", () => {
    const n = makeNode({ enabled: true, active: true, configured_enabled: true });
    const html = buildStateRows(n);
    expect(html).toContain("Enabled");
    expect(html).toContain("Active");
    expect(html).not.toContain("Pending");
  });

  it("enabled but inactive is different from disabled", () => {
    const activeButEnabled = makeNode({ enabled: true, active: false, configured_enabled: true });
    const disabled = makeNode({ enabled: false, active: false, configured_enabled: false });
    const htmlEnabled = buildStateRows(activeButEnabled);
    const htmlDisabled = buildStateRows(disabled);
    // Loaded state differs
    const elEnabled = parseHtml(`<dl>${htmlEnabled}</dl>`);
    const elDisabled = parseHtml(`<dl>${htmlDisabled}</dl>`);
    const dds = (el) => [...el.querySelectorAll("dd")].map(d => d.textContent.trim());
    expect(dds(elEnabled)).not.toEqual(dds(elDisabled));
  });

  it("pending disable shows warning row", () => {
    const n = makeNode({ enabled: true, configured_enabled: false, pending_restart: true });
    const html = buildStateRows(n);
    expect(html).toContain("Pending disable");
    expect(html).toContain("state-pending-warning");
  });
});

// ---------------------------------------------------------------------------
// 2. DOM behaviour tests
// ---------------------------------------------------------------------------

function buildMinimalDom(nodeData, { canChange = true } = {}) {
  // Build the container with data-* attributes
  const root = document.createElement("div");
  root.id = "tree-root";
  root.dataset.graphUrl = "/api/graph/";
  root.dataset.previewUrl = "/api/modules/__slug__/preview/";
  root.dataset.enableUrl  = "/api/modules/__slug__/enable/";
  root.dataset.disableUrl = "/api/modules/__slug__/disable/";
  root.dataset.canChange  = canChange ? "1" : "0";
  document.body.appendChild(root);

  // Detail panel
  const panel = document.createElement("aside");
  panel.id = "detail-panel";
  panel.setAttribute("aria-hidden", "true");
  document.body.appendChild(panel);

  // Build a minimal app object as renderGraph would return
  const el = document.createElement("div");
  el.className = "module-node";
  el.dataset.slug = nodeData.slug;

  const canvas = document.createElement("div");
  canvas.id = "tree-canvas";
  canvas.appendChild(el);
  root.appendChild(canvas);

  const nodeEls = { [nodeData.slug]: { el } };

  const graphData = {
    nodes: [nodeData],
    edges: [],
  };

  const app = { canvas, nodeEls, slugColor: () => "#6366f1" };
  return { root, panel, el, app, graphData };
}

describe("detail panel DOM behaviour", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("clicking a module node adds is-open to the detail panel", () => {
    const n = makeNode();
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();

    expect(panel.classList.contains("is-open")).toBe(true);
    expect(panel.hasAttribute("aria-hidden")).toBe(false);
  });

  it("clicking the close button removes is-open and restores aria-hidden", () => {
    const n = makeNode();
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();
    expect(panel.classList.contains("is-open")).toBe(true);

    const closeBtn = panel.querySelector("#detail-close-btn");
    expect(closeBtn).not.toBeNull();
    closeBtn.click();

    expect(panel.classList.contains("is-open")).toBe(false);
    expect(panel.getAttribute("aria-hidden")).toBe("true");
  });

  it("pending disable shows warning in the detail panel", () => {
    const n = makeNode({ enabled: true, configured_enabled: false, pending_restart: true });
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();

    expect(panel.innerHTML).toContain("Pending disable");
  });

  it("pending disable renders Undo pending disable button", () => {
    const n = makeNode({ enabled: true, configured_enabled: false, pending_restart: true });
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();

    const btn = panel.querySelector("button[data-action='enable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Undo pending disable");
  });

  it("pending enable renders Undo pending enable button", () => {
    const n = makeNode({ enabled: false, active: false, configured_enabled: true, pending_restart: true });
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();

    const btn = panel.querySelector("button[data-action='disable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Undo pending enable");
  });

  it("enabled-but-inactive is displayed differently from disabled", () => {
    const nActive = makeNode({ enabled: true, active: false, configured_enabled: true });
    const nDisabled = makeNode({ slug: "other.mod", enabled: false, active: false, configured_enabled: false });

    // Test enabled-but-inactive
    document.body.innerHTML = "";
    const { el: elA, panel: panA, app: appA, graphData: gdA, root: rootA } = buildMinimalDom(nActive);
    initInteraction(appA, rootA, gdA, { canChange: false });
    elA.click();
    const htmlActive = panA.innerHTML;

    // Test disabled
    document.body.innerHTML = "";
    const { el: elD, panel: panD, app: appD, graphData: gdD, root: rootD } = buildMinimalDom(nDisabled);
    initInteraction(appD, rootD, gdD, { canChange: false });
    elD.click();
    const htmlDisabled = panD.innerHTML;

    // Loaded state must differ
    expect(htmlActive).not.toEqual(htmlDisabled);
    expect(htmlActive).toContain("Enabled");
    expect(htmlDisabled).toContain("Disabled");
  });

  it("no pending override: normal Enable/Disable shown", () => {
    const n = makeNode({ enabled: true, configured_enabled: true, pending_restart: false });
    const { el, panel, app, graphData, root } = buildMinimalDom(n);
    initInteraction(app, root, graphData, { canChange: true });

    el.click();

    const btn = panel.querySelector("button[data-action='disable']");
    expect(btn).not.toBeNull();
    expect(btn.textContent.trim()).toBe("Disable");
  });
});
