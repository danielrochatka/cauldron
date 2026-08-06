/**
 * Regression tests for main.js orchestration concerns:
 * - group-filter population is idempotent across repeated renders
 * - toolbar show/hide via ID (not class) works correctly
 * - focused toolbar controls survive updateFocusedToolbar calls
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

// --------------------------------------------------------------------------- //
// DOM setup helpers                                                            //
// --------------------------------------------------------------------------- //

function makeToolbarDOM() {
  document.body.innerHTML = `
    <div class="tree-toolbar" id="tree-toolbar">
      <select id="group-filter"><option value="">All groups</option></select>
      <select id="state-filter"><option value="">All states</option></select>
    </div>
    <div id="focused-toolbar" style="display:none">
      <button id="btn-show-all-focused">Show all modules</button>
      <button id="btn-fit-focused">Fit to view</button>
      <div id="focused-toolbar-stats"></div>
    </div>
  `;
}

// --------------------------------------------------------------------------- //
// Inline populateGroupFilter implementation (mirrors main.js exactly)         //
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

// Mirrors updateFocusedToolbar from main.js
function updateFocusedToolbar(meta) {
  const toolbar = document.getElementById("focused-toolbar");
  if (!toolbar) return;
  toolbar.style.display = "flex";
  const stats = document.getElementById("focused-toolbar-stats");
  if (stats) {
    stats.innerHTML = `
      <span class="focused-stat"><strong>${meta.selected}</strong></span>
      <span class="focused-stat">${meta.dependencyCount} deps</span>
    `;
  }
  document.getElementById("tree-toolbar")?.style?.setProperty("display", "none");
}

function exitFocusedToolbar() {
  document.getElementById("tree-toolbar")?.style?.removeProperty("display");
  document.getElementById("focused-toolbar")?.style?.setProperty("display", "none");
}

// --------------------------------------------------------------------------- //
// Group filter idempotency                                                     //
// --------------------------------------------------------------------------- //

describe("populateGroupFilter", () => {
  const nodes = [
    { slug: "a", group: "core" },
    { slug: "b", group: "auth" },
    { slug: "c", group: "core" },  // duplicate group
    { slug: "d", group: "" },      // no group — omitted
    { slug: "e", group: null },    // null group — omitted
  ];

  beforeEach(() => {
    makeToolbarDOM();
  });

  it("adds one option per unique non-empty group in sorted order", () => {
    populateGroupFilter(nodes);
    const gf = document.getElementById("group-filter");
    const options = [...gf.options].map((o) => o.value);
    // "All groups" placeholder + sorted unique groups
    expect(options).toEqual(["", "auth", "core"]);
  });

  it("is idempotent — calling twice produces the same options", () => {
    populateGroupFilter(nodes);
    populateGroupFilter(nodes);  // second call must be a no-op
    const gf = document.getElementById("group-filter");
    const groupOptions = [...gf.options].filter((o) => o.value !== "");
    expect(groupOptions).toHaveLength(2);
  });

  it("does not accumulate options across many calls (ten round trips)", () => {
    for (let i = 0; i < 10; i++) {
      populateGroupFilter(nodes);
    }
    const gf = document.getElementById("group-filter");
    const groupOptions = [...gf.options].filter((o) => o.value !== "");
    expect(groupOptions).toHaveLength(2);
  });

  it("omits empty and null groups", () => {
    populateGroupFilter(nodes);
    const gf = document.getElementById("group-filter");
    // Only the pre-existing placeholder option has value ""; no extra empty entry added
    const emptyValueOptions = [...gf.options].filter((o) => o.value === "");
    expect(emptyValueOptions).toHaveLength(1);
    // Only real non-empty groups were appended
    const nonEmptyOptions = [...gf.options].filter((o) => o.value !== "");
    expect(nonEmptyOptions.map((o) => o.value)).toEqual(["auth", "core"]);
  });
});

// --------------------------------------------------------------------------- //
// Toolbar show / hide                                                          //
// --------------------------------------------------------------------------- //

describe("toolbar visibility", () => {
  beforeEach(() => {
    makeToolbarDOM();
  });

  it("#tree-toolbar is addressable by ID", () => {
    expect(document.getElementById("tree-toolbar")).not.toBeNull();
  });

  it("focused toolbar hides #tree-toolbar and shows #focused-toolbar", () => {
    updateFocusedToolbar({ selected: "sel", dependencyCount: 3 });
    const treeToolbar = document.getElementById("tree-toolbar");
    const focusedToolbar = document.getElementById("focused-toolbar");
    expect(treeToolbar.style.display).toBe("none");
    expect(focusedToolbar.style.display).toBe("flex");
  });

  it("focused toolbar controls survive updateFocusedToolbar", () => {
    updateFocusedToolbar({ selected: "sel", dependencyCount: 2 });
    expect(document.getElementById("btn-show-all-focused")).not.toBeNull();
    expect(document.getElementById("btn-fit-focused")).not.toBeNull();
  });

  it("stats text is updated without destroying buttons", () => {
    updateFocusedToolbar({ selected: "sel", dependencyCount: 5 });
    const stats = document.getElementById("focused-toolbar-stats");
    expect(stats.textContent).toContain("sel");
    expect(stats.textContent).toContain("5 deps");
    // Buttons outside the stats div are untouched
    expect(document.getElementById("btn-show-all-focused")).not.toBeNull();
  });

  it("exiting focused mode restores #tree-toolbar and hides #focused-toolbar", () => {
    updateFocusedToolbar({ selected: "sel", dependencyCount: 1 });
    exitFocusedToolbar();
    const treeToolbar = document.getElementById("tree-toolbar");
    const focusedToolbar = document.getElementById("focused-toolbar");
    expect(treeToolbar.style.display).not.toBe("none");
    expect(focusedToolbar.style.display).toBe("none");
  });

  it("repeated focus/unfocus cycles keep both toolbars functional", () => {
    for (let i = 0; i < 5; i++) {
      updateFocusedToolbar({ selected: `mod${i}`, dependencyCount: i });
      exitFocusedToolbar();
    }
    expect(document.getElementById("btn-show-all-focused")).not.toBeNull();
    expect(document.getElementById("btn-fit-focused")).not.toBeNull();
    expect(document.getElementById("tree-toolbar")).not.toBeNull();
  });
});
