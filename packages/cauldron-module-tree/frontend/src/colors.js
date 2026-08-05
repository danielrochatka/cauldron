/**
 * Deterministic FNV-1a slug → color. Mirrors colors.py on the Python side.
 * Same slug always returns the same hex color.
 */
const PALETTE = [
  "#1d4ed8", "#7c3aed", "#0f766e", "#b45309",
  "#c2410c", "#be185d", "#15803d", "#1e40af",
  "#6d28d9", "#0369a1", "#b91c1c", "#0e7490",
  "#854d0e", "#166534", "#9333ea", "#0c4a6e",
];

export function slugColor(slug) {
  let h = 2166136261;
  for (let i = 0; i < slug.length; i++) {
    h ^= slug.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return PALETTE[h % PALETTE.length];
}
