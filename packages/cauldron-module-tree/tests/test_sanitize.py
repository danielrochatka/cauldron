"""Tests for the SVG sanitizer."""
import pytest
from cauldron_module_tree.sanitize import sanitize_svg, is_safe_svg, safe_svg_or_fallback


_CLEAN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>'


def test_valid_svg_passes_through():
    """A simple clean SVG is returned with the expected content."""
    result = sanitize_svg(_CLEAN_SVG)
    assert "circle" in result
    # ElementTree may serialize as <svg ...> or <ns0:svg ...> depending on
    # namespace handling; just check the tag name appears.
    assert "svg" in result


def test_script_tag_removed():
    """<script> element is stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle/></svg>'
    result = sanitize_svg(svg)
    assert "script" not in result
    assert "alert" not in result
    assert "circle" in result


def test_foreign_object_removed():
    """<foreignObject> is stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div>hi</div></foreignObject></svg>'
    result = sanitize_svg(svg)
    assert "foreignObject" not in result
    assert "div" not in result


def test_event_handler_attribute_removed():
    """onclick="..." is stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle onclick="evil()" cx="5" cy="5" r="5"/></svg>'
    result = sanitize_svg(svg)
    assert "onclick" not in result
    assert "evil" not in result
    assert "circle" in result


def test_onload_attribute_removed():
    """onload="..." stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" onload="bad()"><rect/></svg>'
    result = sanitize_svg(svg)
    assert "onload" not in result
    assert "bad" not in result


def test_javascript_href_removed():
    """href="javascript:..." stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><text>click</text></a></svg>'
    result = sanitize_svg(svg)
    assert "javascript:" not in result


def test_external_href_removed():
    """href="http://evil.com" stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><a href="http://evil.com"><text>click</text></a></svg>'
    result = sanitize_svg(svg)
    assert "evil.com" not in result


def test_data_uri_href_removed():
    """href="data:..." stripped."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,abc"/></svg>'
    result = sanitize_svg(svg)
    assert "data:" not in result


def test_invalid_xml_returns_false_for_is_safe():
    """Broken XML returns False from is_safe_svg."""
    assert is_safe_svg("<svg><unclosed") is False


def test_empty_string_returns_false_for_is_safe():
    """Empty string returns False from is_safe_svg."""
    assert is_safe_svg("") is False


def test_fallback_generated_for_invalid_svg():
    """safe_svg_or_fallback('bad', 'my.slug') returns valid SVG."""
    result = safe_svg_or_fallback("bad xml <<<", "my.slug")
    assert "<svg" in result
    assert "</svg>" in result


def test_fallback_uses_first_char_of_slug():
    """Fallback SVG contains first char of slug uppercased."""
    result = safe_svg_or_fallback("", "awesome.module")
    assert "A" in result


def test_deterministic_fallback():
    """Same slug always gives same fallback."""
    result1 = safe_svg_or_fallback("", "my.slug")
    result2 = safe_svg_or_fallback("", "my.slug")
    assert result1 == result2
