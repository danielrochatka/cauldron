"""Tests for HTML/CSS design analysis on fixture files."""
from __future__ import annotations

import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestHtmlAnalysis:
    def test_extracts_title(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert result.title == "Acme Portfolio"

    def test_extracts_headings(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert "Hello, I'm Jane Designer" in result.headings
        assert "Selected Work" in result.headings
        assert "About Me" in result.headings

    def test_extracts_nav_items(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert "Home" in result.nav_items
        assert "About" in result.nav_items

    def test_detects_cards(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert result.uses_cards is True

    def test_extracts_stylesheet_url(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert "/static/style.css" in result.stylesheet_urls

    def test_visible_text_summary_non_empty(self):
        from cauldron_ai_web.analyzer import analyze_html
        html = _read_fixture("reference_site.html")
        result = analyze_html(html)
        assert len(result.visible_text_summary) > 10
        assert "Jane" in result.visible_text_summary

    def test_empty_html_does_not_crash(self):
        from cauldron_ai_web.analyzer import analyze_html
        result = analyze_html("")
        assert result.title == ""
        assert result.headings == ()

    def test_html_without_nav_has_empty_nav_items(self):
        from cauldron_ai_web.analyzer import analyze_html
        result = analyze_html("<html><body><h1>Hello</h1></body></html>")
        assert result.nav_items == ()


class TestCssAnalysis:
    def test_extracts_font_families(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        fonts = [f.lower() for f in result.font_families]
        assert any("inter" in f for f in fonts)
        assert any("playfair" in f for f in fonts)

    def test_extracts_color_hints(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        assert len(result.color_hints) > 0

    def test_extracts_css_variables(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        assert "--color-bg" in result.css_variables
        assert "--color-accent" in result.css_variables

    def test_detects_light_background(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        assert result.background_is_light is True

    def test_detects_cards(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        assert result.uses_cards is True

    def test_border_radius_medium(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        assert result.border_radius_hint == "medium"

    def test_spacing_hint(self):
        from cauldron_ai_web.analyzer import analyze_css
        css = _read_fixture("reference_site.css")
        result = analyze_css(css)
        # reference_site.css has padding values of 16px, 24px, 40px, 32px, 16px, 24px
        # avg > 8 and <= 32 → normal or spacious
        assert result.spacing_hint in ("normal", "spacious")

    def test_analyze_css_with_existing_base(self):
        from cauldron_ai_web.analyzer import DesignCharacteristics, analyze_css
        base = DesignCharacteristics(
            title="My Site",
            headings=("Hello",),
            nav_items=("Home",),
            font_families=(),
            color_hints=(),
            background_is_light=None,
            uses_cards=False,
            border_radius_hint="small",
            spacing_hint="normal",
            visible_text_summary="Some text",
            stylesheet_urls=("/style.css",),
            css_variables={},
        )
        css = _read_fixture("reference_site.css")
        result = analyze_css(css, existing=base)
        assert result.title == "My Site"
        assert result.headings == ("Hello",)
        assert len(result.font_families) > 0

    def test_empty_css_returns_defaults(self):
        from cauldron_ai_web.analyzer import analyze_css
        result = analyze_css("")
        assert result.font_families == ()
        assert result.css_variables == {}
