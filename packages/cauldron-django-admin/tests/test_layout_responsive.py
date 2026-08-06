"""
Automated regression tests for the fluid responsive admin layout.

These tests verify CSS file content and template structure to assert the layout
contract without requiring a headless browser.  They are purely server-side and
run alongside the existing pytest-django test suite.

Section coverage
----------------
- Shell constraints removed (§1): .cui-main__inner no longer has max-width
- Overflow ownership (§2): no overflow-x: hidden on .cui-main
- Width-mode tokens and classes (§3): fluid / readable / narrow modifiers
- Responsive gutters (§4): clamp token present
- min-width: 0 on flex ancestors (§2): prevents invisible overflow
- Dashboard grid (§8): auto-fill + min() allows 4+ columns
- Form layout helpers (§7): readable/wide/field modifiers
- base.html content_width_class block (§3)
- Rendered HTML contracts: pages extend base and expose the width block
"""
import os
import re

import pytest
from django.apps import apps


# --------------------------------------------------------------------------- #
# Helper: read static asset from package                                       #
# --------------------------------------------------------------------------- #

def _css(filename: str) -> str:
    app = apps.get_app_config("cauldron_django_admin")
    path = os.path.join(app.path, "static", "cauldron_admin", "css", filename)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _template(name: str) -> str:
    app = apps.get_app_config("cauldron_django_admin")
    path = os.path.join(app.path, "templates", "cauldron_admin", name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# §1 — Remove global fixed-width ceiling                                       #
# --------------------------------------------------------------------------- #

def test_main_inner_has_no_max_width_constraint():
    """.cui-main__inner must not apply a pixel max-width by default."""
    css = _css("layout.css")
    # The block must exist
    assert ".cui-main__inner" in css
    # max-width: none is the correct value
    inner_block = re.search(r'\.cui-main__inner\s*\{([^}]+)\}', css, re.DOTALL)
    assert inner_block, ".cui-main__inner block not found"
    block = inner_block.group(1)
    assert "max-width: none" in block, (
        ".cui-main__inner must declare max-width: none to allow fluid layout"
    )
    assert "max-width: var(--cui-content-max-width)" not in css, (
        "Old max-width: var(--cui-content-max-width) must be removed from layout.css"
    )


def test_old_content_max_width_token_removed():
    """--cui-content-max-width: 1200px must be removed from tokens.css."""
    css = _css("tokens.css")
    assert "--cui-content-max-width: 1200px" not in css, (
        "--cui-content-max-width: 1200px is still in tokens.css; it should be removed"
    )
    assert "1200px" not in css, (
        "Fixed 1200px value found in tokens.css after fluid layout migration"
    )


def test_content_gutter_uses_clamp():
    """--cui-content-gutter must use a clamp() expression for fluid gutters."""
    css = _css("tokens.css")
    assert "--cui-content-gutter:" in css, (
        "--cui-content-gutter token is missing from tokens.css"
    )
    assert "clamp(" in css, (
        "--cui-content-gutter must use clamp() so gutters scale with viewport width"
    )


def test_main_inner_uses_content_gutter():
    """.cui-main__inner padding must reference --cui-content-gutter."""
    css = _css("layout.css")
    inner_block = re.search(r'\.cui-main__inner\s*\{([^}]+)\}', css, re.DOTALL)
    assert inner_block, ".cui-main__inner block not found"
    assert "--cui-content-gutter" in inner_block.group(1), (
        ".cui-main__inner padding should use var(--cui-content-gutter)"
    )


# --------------------------------------------------------------------------- #
# §2 — Replace clipping with controlled overflow                               #
# --------------------------------------------------------------------------- #

def test_main_no_overflow_x_hidden():
    """.cui-main must not clip content with overflow-x: hidden."""
    css = _css("layout.css")
    main_block = re.search(r'\.cui-main\s*\{([^}]+)\}', css, re.DOTALL)
    assert main_block, ".cui-main block not found in layout.css"
    block = main_block.group(1)
    assert "overflow-x: hidden" not in block, (
        ".cui-main must not use overflow-x: hidden — oversized children "
        "should be reachable via local container scrolling"
    )


def test_main_min_width_zero():
    """.cui-main must have min-width: 0 to prevent flex overflow."""
    css = _css("layout.css")
    main_block = re.search(r'\.cui-main\s*\{([^}]+)\}', css, re.DOTALL)
    assert main_block
    assert "min-width: 0" in main_block.group(1), (
        ".cui-main needs min-width: 0 so flex siblings cannot force it to overflow"
    )


def test_main_inner_min_width_zero():
    """.cui-main__inner must have min-width: 0."""
    css = _css("layout.css")
    inner_block = re.search(r'\.cui-main__inner\s*\{([^}]+)\}', css, re.DOTALL)
    assert inner_block
    assert "min-width: 0" in inner_block.group(1)


def test_content_min_width_zero():
    """.cui-content must have min-width: 0."""
    css = _css("layout.css")
    content_block = re.search(r'\.cui-content\s*\{([^}]+)\}', css, re.DOTALL)
    assert content_block, ".cui-content block not found in layout.css"
    assert "min-width: 0" in content_block.group(1)


def test_table_container_scrolls_locally():
    """.cui-table-container must use overflow-x: auto for local scrolling."""
    css = _css("tables.css")
    assert "overflow-x: auto" in css, (
        ".cui-table-container must scroll horizontally within its own bounds"
    )


# --------------------------------------------------------------------------- #
# §3 — Width-mode modifiers                                                    #
# --------------------------------------------------------------------------- #

def test_readable_width_token_defined():
    """--cui-readable-width token must be defined in tokens.css."""
    css = _css("tokens.css")
    assert "--cui-readable-width:" in css, "--cui-readable-width token missing"


def test_narrow_width_token_defined():
    """--cui-narrow-width token must be defined in tokens.css."""
    css = _css("tokens.css")
    assert "--cui-narrow-width:" in css, "--cui-narrow-width token missing"


def test_content_fluid_modifier_exists():
    """.cui-content--fluid must be defined in layout.css."""
    css = _css("layout.css")
    assert ".cui-content--fluid" in css


def test_content_readable_modifier_uses_token():
    """.cui-content--readable must reference --cui-readable-width."""
    css = _css("layout.css")
    readable = re.search(r'\.cui-content--readable\s*\{([^}]+)\}', css, re.DOTALL)
    assert readable, ".cui-content--readable not found in layout.css"
    assert "--cui-readable-width" in readable.group(1)


def test_content_readable_modifier_is_centered():
    """.cui-content--readable must center itself with margin-inline: auto."""
    css = _css("layout.css")
    readable = re.search(r'\.cui-content--readable\s*\{([^}]+)\}', css, re.DOTALL)
    assert readable
    assert "margin-inline: auto" in readable.group(1), (
        ".cui-content--readable should be centered with margin-inline: auto"
    )


def test_content_narrow_modifier_uses_token():
    """.cui-content--narrow must reference --cui-narrow-width."""
    css = _css("layout.css")
    narrow = re.search(r'\.cui-content--narrow\s*\{([^}]+)\}', css, re.DOTALL)
    assert narrow, ".cui-content--narrow not found in layout.css"
    assert "--cui-narrow-width" in narrow.group(1)


def test_content_width_class_block_in_base_template():
    """base.html must expose a {% block content_width_class %} block on .cui-content."""
    base = _template("base.html")
    assert "content_width_class" in base, (
        "base.html must contain a content_width_class block so child templates "
        "can select fluid / readable / narrow modes"
    )
    # The block must be inside the .cui-content element declaration
    idx_block = base.find("content_width_class")
    idx_content_div = base.rfind("cui-content", 0, idx_block)
    assert idx_content_div > 0, (
        "content_width_class block must appear within the .cui-content div"
    )


# --------------------------------------------------------------------------- #
# §7 — Form layout helpers                                                     #
# --------------------------------------------------------------------------- #

def test_form_layout_readable_class():
    """.cui-form-layout--readable must constrain form internals."""
    css = _css("layout.css")
    assert ".cui-form-layout--readable" in css, (
        ".cui-form-layout--readable missing from layout.css"
    )


def test_form_layout_wide_class():
    """.cui-form-layout--wide must exist."""
    css = _css("layout.css")
    assert ".cui-form-layout--wide" in css


def test_field_short_modifier():
    """.cui-field--short must limit control width."""
    css = _css("layout.css")
    short = re.search(r'\.cui-field--short\s*\{([^}]+)\}', css, re.DOTALL)
    assert short, ".cui-field--short not found"
    assert "max-width" in short.group(1)


def test_field_medium_modifier():
    css = _css("layout.css")
    assert ".cui-field--medium" in css


def test_field_full_modifier():
    css = _css("layout.css")
    assert ".cui-field--full" in css


# --------------------------------------------------------------------------- #
# §8 — Dashboard grid                                                          #
# --------------------------------------------------------------------------- #

def test_dashboard_grid_uses_auto_fill():
    """.cui-dashboard-grid must use auto-fill for responsive column count."""
    css = _css("layout.css")
    grid = re.search(r'\.cui-dashboard-grid\s*\{([^}]+)\}', css, re.DOTALL)
    assert grid, ".cui-dashboard-grid not found"
    assert "auto-fill" in grid.group(1)


def test_dashboard_grid_uses_min_function():
    """.cui-dashboard-grid must use min() to prevent overflow at narrow widths."""
    css = _css("layout.css")
    grid = re.search(r'\.cui-dashboard-grid\s*\{([^}]+)\}', css, re.DOTALL)
    assert grid
    assert "min(100%" in grid.group(1), (
        ".cui-dashboard-grid minmax should use min(100%, Xpx) to prevent "
        "a single card from exceeding its container at narrow widths"
    )


# --------------------------------------------------------------------------- #
# Rendered-HTML contracts                                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_rendered_dashboard_includes_cui_content(client):
    """Rendered dashboard page includes .cui-content in the HTML."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.create_user(username="lyt_dash", password="pw")
    client.force_login(User.objects.get(username="lyt_dash"))
    from django.urls import reverse
    response = client.get(reverse("cauldron:dashboard"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "cui-content" in content


@pytest.mark.django_db
def test_rendered_page_has_no_fixed_max_width_in_inline_style(client):
    """Server-rendered HTML must not carry inline max-width on page containers."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.filter(username="lyt_inline").delete()
    User.objects.create_user(username="lyt_inline", password="pw")
    client.force_login(User.objects.get(username="lyt_inline"))
    from django.urls import reverse
    response = client.get(reverse("cauldron:dashboard"))
    content = response.content.decode()
    # Inline max-width on shell containers is a sign of leftover fixed layout
    assert 'style="max-width: 1200' not in content
    assert 'style="max-width:1200' not in content


@pytest.mark.django_db
def test_rendered_page_content_width_class_block_is_present(client):
    """The cui-content div in rendered HTML contains the content_width_class block
    (rendered as an empty string for default fluid mode)."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.filter(username="lyt_block").delete()
    User.objects.create_user(username="lyt_block", password="pw")
    client.force_login(User.objects.get(username="lyt_block"))
    from django.urls import reverse
    response = client.get(reverse("cauldron:dashboard"))
    content = response.content.decode()
    # Default: cui-content with an empty block — renders as 'class="cui-content "'
    # or 'class="cui-content"' after Django strips trailing spaces.
    assert 'class="cui-content' in content


# --------------------------------------------------------------------------- #
# Viewport-width contract (CSS-level assertions as a proxy for browser tests) #
# --------------------------------------------------------------------------- #

def test_layout_allows_wide_viewport_widths():
    """layout.css must not set any max-width below 1440px on .cui-main__inner.

    This is a proxy assertion for the browser-level measurement that would
    confirm the content region expands from 1440px → 1920px → 2560px.
    A browser test with Playwright would measure actual pixel widths; this
    test verifies the CSS prerequisite.
    """
    css = _css("layout.css")
    inner_block = re.search(r'\.cui-main__inner\s*\{([^}]+)\}', css, re.DOTALL)
    assert inner_block
    block = inner_block.group(1)
    # The only allowed max-width value is "none"
    assert "max-width: none" in block
    # No pixel value should appear alongside max-width in this block
    assert not re.search(r'max-width:\s*\d', block), (
        ".cui-main__inner must not have a pixel max-width — fluid layout required"
    )


def test_responsive_css_has_no_pixel_max_width_on_main_inner():
    """responsive.css must not re-introduce a pixel max-width on .cui-main__inner."""
    css = _css("responsive.css")
    # Find all .cui-main__inner blocks in responsive.css
    blocks = re.findall(r'\.cui-main__inner\s*\{([^}]+)\}', css, re.DOTALL)
    for block in blocks:
        assert not re.search(r'max-width:\s*\d', block), (
            "responsive.css re-introduces a pixel max-width on .cui-main__inner"
        )
