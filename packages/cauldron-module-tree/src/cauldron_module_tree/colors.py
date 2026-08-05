"""Deterministic slug → visual color mapping for the module dependency graph.

Uses FNV-1a hash into a curated 16-color dark palette that passes WCAG AA
contrast against white (#fff) backgrounds.  The same slug always receives the
same color; no lookup table of known slugs is used.
"""
from __future__ import annotations

_PALETTE = [
    "#1d4ed8",  # blue-700
    "#7c3aed",  # violet-600
    "#0f766e",  # teal-700
    "#b45309",  # amber-700
    "#c2410c",  # orange-700
    "#be185d",  # pink-700
    "#15803d",  # green-700
    "#1e40af",  # blue-800
    "#6d28d9",  # purple-700
    "#0369a1",  # sky-700
    "#b91c1c",  # red-700
    "#0e7490",  # cyan-700
    "#854d0e",  # yellow-800
    "#166534",  # green-800
    "#9333ea",  # purple-600
    "#0c4a6e",  # sky-900
]


def slug_color(slug: str) -> str:
    """Return a stable hex color for the given module slug."""
    h = 2166136261  # FNV-1a 32-bit offset basis
    for ch in slug.encode():
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return _PALETTE[h % len(_PALETTE)]
