"""Pure-Python SVG sanitizer using stdlib xml.etree.ElementTree."""
from __future__ import annotations

import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------- #
# Allowlists                                                                   #
# --------------------------------------------------------------------------- #

# Common safe SVG tag local names.  Any tag NOT in this set gets unwrapped
# (children promoted) rather than silently kept.
ALLOWED_SVG_TAGS: frozenset[str] = frozenset({
    "svg",
    "g",
    "path",
    "circle",
    "rect",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "defs",
    "use",
    "symbol",
    "clipPath",
    "mask",
    "linearGradient",
    "radialGradient",
    "stop",
    "title",
    "desc",
    "filter",
    "feBlend",
    "feColorMatrix",
    "feGaussianBlur",
    "feOffset",
    "feMerge",
    "feMergeNode",
    "feTurbulence",
    "feComposite",
    "feFlood",
    "feFuncR",
    "feFuncG",
    "feFuncB",
    "feFuncA",
    "feComponentTransfer",
    "feDisplacementMap",
    "feDiffuseLighting",
    "feSpecularLighting",
    "feDistantLight",
    "fePointLight",
    "feSpotLight",
})

# --------------------------------------------------------------------------- #
# Forbidden tag local names — removed entirely at every level                  #
# --------------------------------------------------------------------------- #

_FORBIDDEN_TAG_NAMES: frozenset[str] = frozenset({
    "script",
    "foreignObject",
    "animate",
    "animateMotion",
    "animateTransform",
    "animateColor",
    "set",
    "handler",
    "listener",
    # Non-SVG roots are also forbidden in the root-check below
    "html",
})

# SVG namespace URI
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

# Href attribute names (Clark notation and bare).
_HREF_ATTRS: frozenset[str] = frozenset({
    "href",
    f"{{{_XLINK_NS}}}href",
})

# Unsafe URL scheme prefixes (checked after lowercasing + stripping whitespace
# and null bytes).
_UNSAFE_SCHEMES: tuple[str, ...] = (
    "javascript:",
    "vbscript:",
    "data:",
    "blob:",
)

# External href schemes that are forbidden (pointing outside the document).
_EXTERNAL_HREF_PREFIXES: tuple[str, ...] = ("http", "https", "//")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _local_name(tag: str) -> str:
    """Return the local name of *tag*, stripping any Clark-notation namespace."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _ns_uri(tag: str) -> str | None:
    """Return the namespace URI from a Clark-notation tag, or None."""
    if tag.startswith("{"):
        return tag.split("}", 1)[0][1:]
    return None


def _is_forbidden_tag(tag: str) -> bool:
    return _local_name(tag) in _FORBIDDEN_TAG_NAMES


def _normalize_url_value(value: str) -> str:
    """Strip null bytes and whitespace, then lowercase for scheme comparison."""
    return value.replace("\x00", "").strip().lower()


def _is_unsafe_attr(name: str, value: str) -> bool:
    """Return True if the attribute should be removed."""
    local = _local_name(name)

    # Remove event handlers (on* case-insensitively by local name).
    if local.lower().startswith("on"):
        return True

    # Remove attributes with dangerous URL scheme values.
    norm_val = _normalize_url_value(value)
    if any(norm_val.startswith(p) for p in _UNSAFE_SCHEMES):
        return True

    # Remove href/xlink:href pointing to external resources.
    if name in _HREF_ATTRS:
        stripped = value.strip()
        lower_stripped = stripped.lower()
        if lower_stripped.startswith("http://") or lower_stripped.startswith("https://") or stripped.startswith("//"):
            return True

    return False


def _is_xmlns_unsafe(name: str, value: str) -> bool:
    """Return True if the attribute is a xmlns:* binding to a non-SVG/non-XLINK NS."""
    local = _local_name(name)
    full_lower = name.lower()
    if full_lower.startswith("xmlns") and ":" in full_lower:
        # e.g. xmlns:foo="http://evil.com"
        ns_val = value.strip()
        if ns_val not in (_SVG_NS, _XLINK_NS, ""):
            return True
    return False


# --------------------------------------------------------------------------- #
# Recursive sanitizer                                                           #
# --------------------------------------------------------------------------- #

def _collect_children_recursive(element: ET.Element) -> list[ET.Element]:
    """Collect all direct children of *element* as a flat list."""
    return list(element)


def _walk_and_sanitize(parent: ET.Element) -> None:
    """Recursively remove forbidden elements, unwrap unknown elements,
    and strip unsafe attributes — in-place."""

    to_remove: list[ET.Element] = []
    to_unwrap: list[tuple[int, ET.Element]] = []  # (index, element) pairs

    for idx, child in enumerate(list(parent)):
        local = _local_name(child.tag)

        if _is_forbidden_tag(child.tag):
            to_remove.append(child)
            continue

        if local not in ALLOWED_SVG_TAGS:
            # Unwrap: promote children, discard the wrapper element
            to_unwrap.append((idx, child))
            continue

        # Sanitize attributes
        unsafe_attrs = [
            k for k, v in child.attrib.items()
            if _is_unsafe_attr(k, v) or _is_xmlns_unsafe(k, v)
        ]
        for attr in unsafe_attrs:
            del child.attrib[attr]

        # Recurse into kept element
        _walk_and_sanitize(child)

    # Remove forbidden elements (no child promotion)
    for child in to_remove:
        parent.remove(child)

    # Unwrap unknown elements: insert their (sanitized) children in their place.
    # Process in reverse index order so insertions don't shift subsequent positions.
    for idx, child in reversed(to_unwrap):
        # First recursively sanitize the children of the wrapper
        _walk_and_sanitize(child)
        grandchildren = list(child)
        parent.remove(child)
        for offset, gc in enumerate(grandchildren):
            parent.insert(idx + offset, gc)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def sanitize_svg(svg: str) -> str:
    """Return a sanitized copy of *svg* with dangerous elements and attributes removed.

    Parses using the stdlib ``xml.etree.ElementTree``.
    Raises ``ET.ParseError`` on malformed XML.
    Raises ``ValueError("Root element must be <svg>")`` if the root is not an
    ``<svg>`` element (by local name).
    """
    root = ET.fromstring(svg)  # noqa: S314 — intentional stdlib parse

    # Enforce SVG root
    root_local = _local_name(root.tag)
    if root_local != "svg":
        raise ValueError("Root element must be <svg>")

    # Sanitize root-level attributes (including on*, unsafe URLs, xmlns bindings)
    unsafe_root_attrs = [
        k for k, v in root.attrib.items()
        if _is_unsafe_attr(k, v) or _is_xmlns_unsafe(k, v)
    ]
    for attr in unsafe_root_attrs:
        del root.attrib[attr]

    # Sanitize children recursively
    _walk_and_sanitize(root)

    return ET.tostring(root, encoding="unicode")


def is_safe_svg(svg: str) -> bool:
    """Return True if *svg* is non-empty and can be successfully sanitized."""
    if not svg or not svg.strip():
        return False
    try:
        sanitize_svg(svg)
        return True
    except (ValueError, ET.ParseError):
        return False


def safe_svg_or_fallback(svg: str, slug: str) -> str:
    """Return the sanitized *svg* if safe; otherwise a deterministic fallback SVG.

    The fallback uses the first character of *slug* (uppercased) displayed
    inside a grey circle.
    """
    if is_safe_svg(svg):
        return sanitize_svg(svg)
    letter = (slug[0].upper()) if slug else "?"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        f'<circle cx="20" cy="20" r="18" fill="#6B7280"/>'
        f'<text x="20" y="26" text-anchor="middle" font-size="18" fill="white">{letter}</text>'
        f"</svg>"
    )
