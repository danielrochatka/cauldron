"""Pure-Python SVG sanitizer using stdlib xml.etree.ElementTree."""
from __future__ import annotations

import xml.etree.ElementTree as ET

# Tag local names that must be removed entirely (along with their subtree).
_FORBIDDEN_TAG_NAMES = frozenset({
    "script",
    "foreignObject",
    "animate",
    "animateMotion",
    "animateTransform",
    "set",
})

# Attribute value prefixes that indicate unsafe content.
_UNSAFE_VALUE_PREFIXES = ("javascript:", "data:", "vbscript:")

# Href attribute names (Clark notation and bare).
_HREF_ATTRS = frozenset({
    "href",
    "{http://www.w3.org/1999/xlink}href",
})

# External resource prefixes for href values.
_EXTERNAL_PREFIXES = ("http", "//")


def _local_name(tag: str) -> str:
    """Return the local name of *tag*, stripping any Clark-notation namespace."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _is_forbidden_tag(tag: str) -> bool:
    return _local_name(tag) in _FORBIDDEN_TAG_NAMES


def _is_unsafe_attr(name: str, value: str) -> bool:
    """Return True if the attribute should be removed."""
    local = _local_name(name)
    # Remove event handlers.
    if local.startswith("on"):
        return True
    # Remove attributes with dangerous value prefixes.
    lower_val = value.strip().lower()
    if any(lower_val.startswith(p) for p in _UNSAFE_VALUE_PREFIXES):
        return True
    # Remove href/xlink:href pointing to external resources.
    if name in _HREF_ATTRS and any(value.startswith(p) for p in _EXTERNAL_PREFIXES):
        return True
    return False


def _walk_and_sanitize(parent: ET.Element) -> None:
    """Recursively remove forbidden elements and unsafe attributes in-place."""
    # Collect children to remove (cannot mutate while iterating).
    to_remove: list[ET.Element] = []
    for child in list(parent):
        if _is_forbidden_tag(child.tag):
            to_remove.append(child)
        else:
            # Sanitize attributes on this element.
            unsafe_attrs = [
                k for k, v in child.attrib.items() if _is_unsafe_attr(k, v)
            ]
            for attr in unsafe_attrs:
                del child.attrib[attr]
            # Recurse.
            _walk_and_sanitize(child)

    for child in to_remove:
        parent.remove(child)


def sanitize_svg(svg: str) -> str:
    """Return a sanitized copy of *svg* with dangerous elements and attributes removed.

    Parses using the stdlib ``xml.etree.ElementTree`` (not defusedxml).
    Raises ``ET.ParseError`` or ``ValueError`` on malformed input.
    """
    root = ET.fromstring(svg)  # noqa: S314 — intentional stdlib parse

    # Sanitize root-level attributes.
    unsafe_root_attrs = [k for k, v in root.attrib.items() if _is_unsafe_attr(k, v)]
    for attr in unsafe_root_attrs:
        del root.attrib[attr]

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
