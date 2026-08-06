/**
 * Pure focused-subgraph operations.
 * No DOM. No ELK. No side effects.
 *
 * buildFocusedSubgraph(data, slug) derives the reduced graph entirely from the
 * already-loaded full graph data so no additional HTTP request is needed.
 *
 * Inclusion rules
 * ---------------
 * - selected  : the chosen slug
 * - requires  : direct requirements of selected (one hop forward only, all kinds)
 * - used_by   : full transitive reverse closure (all modules that depend on selected)
 *
 * Missing targets
 * ---------------
 * If a direct requirement edge points to a slug that is not registered, a
 * synthetic terminal node is added.  Synthetic nodes have state "missing" and
 * are not recursed into.
 *
 * Requires edges
 * --------------
 * The semantic direction is selected → requirement ("requires"), but reversed
 * layout edges (requirement → selected) are included in layoutEdges so ELK
 * positions requirements ABOVE the selected node.  The renderer calls
 * buildReversedElkPath for kind "requires" to flip the geometry.
 *
 * Used-by edges
 * -------------
 * The semantic direction is consumer → selected (a consumer depends on selected).
 * Both layout and display use the REVERSED direction (dependency → consumer)
 * so ELK places consumers BELOW selected and arrows flow downward.
 * The renderer calls buildElkPath (no reversal needed).
 *
 * Returns
 * -------
 * {
 *   nodes          – focused node list with added focus_role
 *   layoutEdges    – edges for ELK (reversed requires_layout + reversed used_by)
 *   displayEdges   – all edges for rendering (semantic requires + reversed used_by)
 *   roles          – { [slug]: "selected"|"requires"|"used_by" }
 *   missingTargets – Set of slugs synthesised as missing terminals
 *   metadata       – { selected, requiresCount, usedByCount, missingCount,
 *                      requiresList, usedByList }
 * }
 */
export function buildFocusedSubgraph(data, slug) {
  const nodeMap = Object.fromEntries(data.nodes.map((n) => [n.slug, n]));
  if (!nodeMap[slug]) {
    throw new Error(`Module not found: ${slug}`);
  }

  // Build reverse adjacency from full graph edges — all edge kinds
  const revAll = {};   // slug → [{ source, kind }]  (all incoming)
  for (const e of data.edges) {
    (revAll[e.target] ||= []).push({ source: e.source, kind: e.kind });
  }

  // Direct requires: one-hop forward from selected, deduplicating by target.
  // Store the full first edge per target so capability and status are preserved.
  const requiresEdgeMap = new Map(); // target → first complete edge object
  const missingTargets = new Set();
  for (const e of data.edges) {
    if (e.source !== slug || e.target === slug) continue;
    if (nodeMap[e.target]) {
      if (!requiresEdgeMap.has(e.target)) requiresEdgeMap.set(e.target, e);
    } else {
      missingTargets.add(e.target);
    }
  }
  const requiresSlugs = new Set(requiresEdgeMap.keys());

  // Used-by closure: full transitive reverse BFS from selected (all edge kinds)
  const usedByClosure = new Set();
  const ubVisited = new Set([slug]);
  const ubQueue = [slug];
  while (ubQueue.length) {
    const current = ubQueue.shift();
    for (const { source: consumer } of revAll[current] || []) {
      if (!ubVisited.has(consumer) && nodeMap[consumer]) {
        ubVisited.add(consumer);
        usedByClosure.add(consumer);
        ubQueue.push(consumer);
      }
    }
  }

  // Requires takes priority over used_by (resolves mutual-dependency cycles)
  for (const req of requiresSlugs) usedByClosure.delete(req);

  // Roles
  const roles = { [slug]: "selected" };
  for (const req of requiresSlugs) roles[req] = "requires";
  for (const m of missingTargets) roles[m] = "requires";
  for (const ub of usedByClosure) roles[ub] = "used_by";

  const focusedSlugs = new Set([slug, ...requiresSlugs, ...usedByClosure]);

  // Focused real nodes with focus_role attached
  const realNodes = data.nodes
    .filter((n) => focusedSlugs.has(n.slug))
    .map((n) => ({ ...n, focus_role: roles[n.slug] }));

  // Synthetic terminal nodes for missing requires targets
  const syntheticNodes = [...missingTargets].sort().map((target) => ({
    slug: target,
    title: `Missing: ${target}`,
    state: "missing",
    version: "",
    group: "",
    summary: "",
    icon_svg: null,
    visual_color: "#9ca3af",
    enabled: false,
    active: false,
    configured_enabled: false,
    pending_restart: false,
    errors: [],
    focus_role: "requires",
    isSynthetic: true,
  }));

  const nodes = [...realNodes, ...syntheticNodes];

  // Requires layout edges: REVERSED (req → slug) so ELK positions requirements above selected.
  // Requires display edges: semantic direction (slug → req), renderer reverses ELK geometry.
  // Both arrays must be in the same order so indices align.
  const sortedReqSlugs = [...requiresSlugs].sort();
  const sortedMissing = [...missingTargets].sort();

  const requiresLayoutEdges = [
    ...sortedReqSlugs.map((req) => ({
      source: req,
      target: slug,
      kind: "requires_layout",
      capability: null,
      status: "resolved",
    })),
    ...sortedMissing.map((m) => ({
      source: m,
      target: slug,
      kind: "requires_layout",
      capability: null,
      status: "resolved",
    })),
  ];

  const requiresDisplayEdges = [
    ...sortedReqSlugs.map((req) => {
      const orig = requiresEdgeMap.get(req);
      return {
        source: slug,
        target: req,
        kind: "requires",
        relationship_kind: orig.kind,    // original edge kind (required/optional/capability)
        capability: orig.capability ?? null,
        status: orig.status,             // preserves blocked/conflict/missing
      };
    }),
    ...sortedMissing.map((m) => ({
      source: slug,
      target: m,
      kind: "requires",
      capability: null,
      status: "missing",
    })),
  ];

  // Used-by edges: reversed original edges within the used-by closure.
  // Original: consumer → dependency; Reversed: dependency → consumer
  // The same reversed edges are used for both layout and display so ELK
  // places consumers below selected without needing path reversal in the renderer.
  // requiresSlugs is included in usedBySet so that a node removed from
  // usedByClosure due to cycle priority can still serve as a bridge target
  // for consumers that depend on it (e.g. a→b, b→a, c→b focusing a: c→b preserved).
  const usedBySet = new Set([slug, ...usedByClosure, ...requiresSlugs]);
  const usedByEdges = data.edges
    .filter((e) => usedByClosure.has(e.source) && usedBySet.has(e.target))
    .map((e) => ({
      source: e.target,  // reversed: dependency becomes source
      target: e.source,  // reversed: consumer becomes target
      kind: "used_by",
      capability: e.capability,
      status: e.status,
    }));

  // layoutEdges and displayEdges MUST share the same indices (critical for renderer
  // ELK geometry lookup via edge_${idx}).
  // requires section (indices 0..N-1): layout[i] ≠ display[i] in direction;
  //   renderer calls buildReversedElkPath for "requires" display edges.
  // used_by section (indices N..): layout[i] === display[i];
  //   renderer calls buildElkPath directly.
  const layoutEdges = [...requiresLayoutEdges, ...usedByEdges];
  const displayEdges = [...requiresDisplayEdges, ...usedByEdges];

  // Module name lists for the detail panel.
  // Missing targets are included so requiresCount matches the list length.
  const requiresList = [
    ...sortedReqSlugs.map((req) => ({
      slug: req,
      name: nodeMap[req]?.title || req,
    })),
    ...sortedMissing.map((m) => ({
      slug: m,
      name: `Missing: ${m}`,
      isMissing: true,
    })),
  ];
  const usedByList = [...usedByClosure].sort().map((ub) => ({
    slug: ub,
    name: nodeMap[ub]?.title || ub,
  }));

  return {
    nodes,
    layoutEdges,
    displayEdges,
    roles,
    missingTargets,
    metadata: {
      selected: slug,
      requiresCount: requiresSlugs.size + missingTargets.size,
      usedByCount: usedByClosure.size,
      requiresList,
      usedByList,
      missingCount: missingTargets.size,
    },
  };
}

/**
 * Cache for focused ELK layouts.
 * Key: slug + "@" + graph revision token.
 */
export function makeFocusedLayoutCache() {
  const cache = new Map();
  return {
    get(slug, revision) { return cache.get(`${slug}@${revision}`); },
    set(slug, revision, layout) { cache.set(`${slug}@${revision}`, layout); },
    clear() { cache.clear(); },
  };
}
