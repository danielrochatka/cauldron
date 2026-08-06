/**
 * Pure focused-subgraph operations.
 * No DOM. No ELK. No side effects.
 *
 * buildFocusedSubgraph(data, slug) derives the reduced graph entirely from the
 * already-loaded full graph data so no additional HTTP request is needed.
 *
 * Inclusion rules
 * ---------------
 * - selected      : the chosen slug
 * - dependency    : full transitive closure (all edge kinds: required,
 *                   capability, optional)
 * - parent_context: ALL direct incoming dependency relationships to the
 *                   selected module (required, capability, and optional)
 *
 * Missing targets
 * ---------------
 * If a dependency edge points to a slug that is not registered, a synthetic
 * terminal node is added so the relationship remains visible in the focused
 * view.  Synthetic nodes have state "missing" and are never recursed into.
 *
 * Parent-context edges
 * --------------------
 * Each parent_context edge carries the original relationship_kind
 * ("required", "optional", "capability") so the renderer can preserve
 * line-style semantics.  The semantic direction is selected → parent ("used
 * by"), but a reversed layout edge (parent → selected) is included in
 * layoutEdges so ELK positions parents above the selected node.
 *
 * Returns
 * -------
 * {
 *   nodes          – focused node list with added focus_role
 *   layoutEdges    – edges for ELK (dep edges + reversed parent_context_layout)
 *   displayEdges   – all edges for rendering (dep edges + parent_context)
 *   roles          – { [slug]: "selected"|"dependency"|"parent_context" }
 *   missingTargets – Set of slugs that were synthesised as missing terminals
 *   metadata       – { selected, dependencyCount, parentCount, missingCount, maxDepth }
 * }
 */
export function buildFocusedSubgraph(data, slug) {
  const nodeMap = Object.fromEntries(data.nodes.map((n) => [n.slug, n]));
  if (!nodeMap[slug]) {
    throw new Error(`Module not found: ${slug}`);
  }

  // Build adjacency from full graph edges — all edge kinds
  const fwdAll = {};   // slug → [target slugs]
  const revAll = {};   // slug → [{ source, kind }]  (all incoming)
  for (const e of data.edges) {
    (fwdAll[e.source] ||= []).push(e.target);
    (revAll[e.target] ||= []).push({ source: e.source, kind: e.kind });
  }

  // BFS forward to collect full transitive dependency closure.
  // Real nodes enter the queue; missing-target stubs are noted but not recursed.
  const depClosure = new Set();
  const missingTargets = new Set();
  const visited = new Set([slug]);
  const queue = [slug];
  while (queue.length) {
    const current = queue.shift();
    for (const neighbor of fwdAll[current] || []) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        if (nodeMap[neighbor]) {
          depClosure.add(neighbor);
          queue.push(neighbor);
        } else if (neighbor !== slug) {
          // Missing dependency: synthetic terminal node, no recursion
          missingTargets.add(neighbor);
        }
      }
    }
  }

  // Direct parents: ALL modules with any incoming edge to the selected module.
  // Store the original relationship kind for visual semantics.
  const parentInfo = new Map(); // slug → relationship kind
  for (const { source: parent, kind } of revAll[slug] || []) {
    if (nodeMap[parent] && !depClosure.has(parent) && parent !== slug) {
      if (!parentInfo.has(parent)) parentInfo.set(parent, kind);
    }
  }
  const parentSlugs = new Set(parentInfo.keys());

  // Roles
  const roles = { [slug]: "selected" };
  for (const dep of depClosure) roles[dep] = "dependency";
  for (const miss of missingTargets) roles[miss] = "dependency";
  for (const p of parentSlugs) roles[p] = "parent_context";

  const depOnlySlugs = new Set([slug, ...depClosure]);
  const focusedSlugs = new Set([slug, ...depClosure, ...parentSlugs]);

  // Focused real nodes with focus_role attached
  const realNodes = data.nodes
    .filter((n) => focusedSlugs.has(n.slug))
    .map((n) => ({ ...n, focus_role: roles[n.slug] }));

  // Synthetic terminal nodes for missing targets
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
    focus_role: "dependency",
    isSynthetic: true,
  }));

  const nodes = [...realNodes, ...syntheticNodes];

  // Dependency edges — within dep-only set OR pointing to a missing target
  const depEdges = data.edges.filter(
    (e) =>
      depOnlySlugs.has(e.source) &&
      (depOnlySlugs.has(e.target) || missingTargets.has(e.target)),
  );

  // Parent-context display edges (selected → parent, semantic "used by")
  const parentContextEdges = [...parentSlugs].sort().map((parent) => ({
    source: slug,
    target: parent,
    kind: "parent_context",
    relationship_kind: parentInfo.get(parent),
    direction_label: "used by",
    capability: null,
    status: "resolved",
  }));

  // Parent-context layout edges for ELK: REVERSED (parent → selected)
  // so ELK's layered DOWN algorithm places parents above the selected node.
  const parentLayoutEdges = [...parentSlugs].sort().map((parent) => ({
    source: parent,
    target: slug,
    kind: "parent_context_layout",
    capability: null,
    status: "resolved",
  }));

  // layoutEdges: dep edges + reversed parent layout edges
  // displayEdges: dep edges + semantic parent_context edges
  // Both arrays share the same indices for dep edges (0..n-1) and parent edges
  // (n..n+k), which is critical for the renderer to look up ELK routing by index.
  const layoutEdges = [...depEdges, ...parentLayoutEdges];
  const displayEdges = [...depEdges, ...parentContextEdges];

  // Max dependency depth via BFS from selected
  let maxDepth = 0;
  const depVisited = new Set([slug]);
  const depQueue = [[slug, 0]];
  while (depQueue.length) {
    const [current, depth] = depQueue.shift();
    if (depth > maxDepth) maxDepth = depth;
    for (const neighbor of fwdAll[current] || []) {
      if (depClosure.has(neighbor) && !depVisited.has(neighbor)) {
        depVisited.add(neighbor);
        depQueue.push([neighbor, depth + 1]);
      }
    }
  }

  return {
    nodes,
    layoutEdges,
    displayEdges,
    roles,
    missingTargets,
    metadata: {
      selected: slug,
      dependencyCount: depClosure.size,
      parentCount: parentSlugs.size,
      missingCount: missingTargets.size,
      maxDepth,
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
