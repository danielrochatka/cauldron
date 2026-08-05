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
 * - dependency    : full transitive closure (required + capability + optional)
 * - parent_context: direct dependents of the selected module (one level only)
 *
 * Returns an object with:
 *   nodes        – focused node list with added focus_role
 *   layoutEdges  – edges for ELK (dependency direction only; parents added as
 *                  reversed edges so ELK positions them above the selected node)
 *   displayEdges – all edges for rendering (includes parent_context edges)
 *   roles        – { [slug]: "selected"|"dependency"|"parent_context" }
 *   metadata     – { selected, dependencyCount, parentCount, maxDepth }
 */
export function buildFocusedSubgraph(data, slug) {
  const nodeMap = Object.fromEntries(data.nodes.map((n) => [n.slug, n]));
  if (!nodeMap[slug]) {
    throw new Error(`Module not found: ${slug}`);
  }

  // Build adjacency from full graph edges
  const fwdAll = {};   // slug → [target slugs] (required + capability + optional)
  const revRequired = {};  // slug → [source slugs] (required + capability only)
  for (const e of data.edges) {
    (fwdAll[e.source] ||= []).push(e.target);
    if (e.kind === "required" || e.kind === "capability") {
      (revRequired[e.target] ||= []).push(e.source);
    }
  }

  // BFS forward from slug to collect full transitive dependency closure
  const depClosure = new Set();
  const visited = new Set([slug]);
  const queue = [slug];
  while (queue.length) {
    const current = queue.shift();
    for (const neighbor of fwdAll[current] || []) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        if (nodeMap[neighbor]) {
          // Only track real nodes as dependencies; unknown targets are skipped
          depClosure.add(neighbor);
          queue.push(neighbor);
        }
      }
    }
  }

  // Direct parents: modules that require the selected module (one level only)
  const parentSlugs = new Set();
  for (const parent of revRequired[slug] || []) {
    if (nodeMap[parent] && !depClosure.has(parent) && parent !== slug) {
      parentSlugs.add(parent);
    }
  }

  // Roles
  const roles = { [slug]: "selected" };
  for (const dep of depClosure) roles[dep] = "dependency";
  for (const p of parentSlugs) roles[p] = "parent_context";

  const depOnlySlugs = new Set([slug, ...depClosure]);
  const focusedSlugs = new Set([slug, ...depClosure, ...parentSlugs]);

  // Focused node list with focus_role attached
  const nodes = data.nodes
    .filter((n) => focusedSlugs.has(n.slug))
    .map((n) => ({ ...n, focus_role: roles[n.slug] }));

  // Dependency edges (within dep-only set): used for ELK layout AND rendering
  const depEdges = data.edges.filter(
    (e) => depOnlySlugs.has(e.source) && depOnlySlugs.has(e.target),
  );

  // Parent-context edges for rendering only (source=selected, target=parent)
  const parentContextEdges = [...parentSlugs].sort().map((parent) => ({
    source: slug,
    target: parent,
    kind: "parent_context",
    direction_label: "used by",
    capability: null,
    status: "resolved",
  }));

  // ELK layout edges:
  //   - dependency edges flow downward (selected → deps)
  //   - parent nodes need to appear ABOVE selected, so we add a REVERSED
  //     layout-only edge (parent → selected); ELK with DIR=DOWN will place
  //     the source (parent) above the target (selected).
  const parentLayoutEdges = [...parentSlugs].sort().map((parent) => ({
    source: parent,
    target: slug,
    kind: "parent_context_layout",   // internal; not rendered
    direction_label: null,
    capability: null,
    status: "resolved",
  }));

  const layoutEdges = [...depEdges, ...parentLayoutEdges];
  const displayEdges = [...depEdges, ...parentContextEdges];

  // Max dependency depth (BFS from selected through dep edges)
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
    metadata: {
      selected: slug,
      dependencyCount: depClosure.size,
      parentCount: parentSlugs.size,
      maxDepth,
    },
  };
}

/**
 * Cache for focused ELK layouts.
 * Key: slug + "@" + graph revision token.
 * Returns cached layout or undefined.
 */
export function makeFocusedLayoutCache() {
  const cache = new Map();
  return {
    get(slug, revision) { return cache.get(`${slug}@${revision}`); },
    set(slug, revision, layout) { cache.set(`${slug}@${revision}`, layout); },
    clear() { cache.clear(); },
  };
}
