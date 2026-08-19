"""Design characteristic extraction from HTML/CSS."""
from __future__ import annotations

import html.parser
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 5000
_MAX_HEADINGS = 30
_MAX_NAV_ITEMS = 20


@dataclass(frozen=True)
class DesignCharacteristics:
    title: str
    headings: tuple[str, ...]
    nav_items: tuple[str, ...]
    font_families: tuple[str, ...]
    color_hints: tuple[str, ...]
    background_is_light: bool | None
    uses_cards: bool
    border_radius_hint: str  # "none", "small", "medium", "rounded"
    spacing_hint: str  # "compact", "normal", "spacious"
    visible_text_summary: str
    stylesheet_urls: tuple[str, ...]
    css_variables: dict  # CSS custom properties found


def analyze_html(html_content: str, *, base_url: str = "") -> DesignCharacteristics:
    parser = _DesignHTMLParser()
    try:
        parser.feed(html_content[:500_000])
    except Exception:
        pass  # best-effort

    title = parser.title[:200] if parser.title else ""
    headings = tuple(parser.headings[:_MAX_HEADINGS])
    nav_items = tuple(parser.nav_items[:_MAX_NAV_ITEMS])
    stylesheet_urls = tuple(parser.stylesheet_urls[:10])
    visible_text = " ".join(parser.visible_text_parts)[:_MAX_TEXT_CHARS]

    return DesignCharacteristics(
        title=title,
        headings=headings,
        nav_items=nav_items,
        font_families=(),
        color_hints=(),
        background_is_light=None,
        uses_cards=_detect_cards_in_html(html_content),
        border_radius_hint="small",
        spacing_hint="normal",
        visible_text_summary=visible_text,
        stylesheet_urls=stylesheet_urls,
        css_variables={},
    )


def analyze_css(css_content: str, *, existing: DesignCharacteristics | None = None) -> DesignCharacteristics:
    font_families = _extract_font_families(css_content)
    color_hints = _extract_color_hints(css_content)
    css_variables = _extract_css_variables(css_content)
    background_is_light = _guess_background_light(css_variables, color_hints)
    border_radius_hint = _guess_border_radius(css_content)
    spacing_hint = _guess_spacing(css_content)
    uses_cards = _detect_cards_in_css(css_content)

    base = existing or DesignCharacteristics(
        title="",
        headings=(),
        nav_items=(),
        font_families=(),
        color_hints=(),
        background_is_light=None,
        uses_cards=False,
        border_radius_hint="small",
        spacing_hint="normal",
        visible_text_summary="",
        stylesheet_urls=(),
        css_variables={},
    )

    return DesignCharacteristics(
        title=base.title,
        headings=base.headings,
        nav_items=base.nav_items,
        font_families=tuple(font_families[:10]),
        color_hints=tuple(color_hints[:20]),
        background_is_light=background_is_light,
        uses_cards=uses_cards or base.uses_cards,
        border_radius_hint=border_radius_hint,
        spacing_hint=spacing_hint,
        visible_text_summary=base.visible_text_summary,
        stylesheet_urls=base.stylesheet_urls,
        css_variables={k: v for k, v in list(css_variables.items())[:50]},
    )


class _DesignHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.headings: list[str] = []
        self.nav_items: list[str] = []
        self.stylesheet_urls: list[str] = []
        self.visible_text_parts: list[str] = []
        self._in_title = False
        self._in_nav = False
        self._in_skip = False
        self._skip_tags = {"script", "style", "noscript", "svg", "head"}
        self._current_heading: str | None = None
        self._heading_text: list[str] = []
        self._nav_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attrs_dict = dict(attrs)
        tag = tag.lower()

        if tag in self._skip_tags:
            self._in_skip = True
        if tag == "title":
            self._in_title = True
        if tag in ("h1", "h2", "h3", "h4"):
            self._current_heading = tag
            self._heading_text = []
        if tag == "nav":
            self._in_nav = True
            self._nav_depth += 1
        if tag == "link" and attrs_dict.get("rel") == "stylesheet":
            href = attrs_dict.get("href", "")
            if href and not href.startswith("data:"):
                self.stylesheet_urls.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._skip_tags:
            self._in_skip = False
        if tag == "title":
            self._in_title = False
        if tag in ("h1", "h2", "h3", "h4") and self._current_heading:
            text = " ".join(self._heading_text).strip()
            if text and len(text) < 200:
                self.headings.append(text)
            self._current_heading = None
            self._heading_text = []
        if tag == "nav":
            self._nav_depth = max(0, self._nav_depth - 1)
            if self._nav_depth == 0:
                self._in_nav = False

    def handle_data(self, data: str) -> None:
        # Title is extracted even when inside the skipped <head> block.
        if self._in_title:
            text = data.strip()
            if text:
                self.title = (self.title + " " + text).strip()
        if self._in_skip:
            return
        text = data.strip()
        if not text:
            return
        if self._current_heading is not None:
            self._heading_text.append(text)
        if self._in_nav and text and len(text) < 100:
            self.nav_items.append(text)
        if len(text) > 3:
            self.visible_text_parts.append(text)


def _extract_font_families(css: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r'font-family\s*:\s*([^;}{]+)', css, re.IGNORECASE):
        raw = match.group(1).strip()
        for part in raw.split(","):
            font = part.strip().strip("'\"")
            if font and font.lower() not in ("inherit", "initial", "unset", "var"):
                found.append(font[:80])
    seen: set[str] = set()
    unique: list[str] = []
    for f in found:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _extract_color_hints(css: str) -> list[str]:
    colors: list[str] = []
    for match in re.finditer(
        r'(?:color|background(?:-color)?|fill)\s*:\s*([^;}{]+)', css, re.IGNORECASE
    ):
        val = match.group(1).strip()
        if val and not val.startswith("var(") and val.lower() not in (
            "inherit", "transparent", "initial"
        ):
            colors.append(val[:50])
    return list(dict.fromkeys(colors))


def _extract_css_variables(css: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r'(--[\w-]+)\s*:\s*([^;}{]+)', css):
        name = match.group(1).strip()
        value = match.group(2).strip()
        result[name] = value[:100]
    return result


def _guess_background_light(variables: dict, color_hints: list) -> bool | None:
    bg = (
        variables.get("--color-bg")
        or variables.get("--background")
        or variables.get("--bg-color")
    )
    if bg:
        bg = bg.strip().lower()
        if "#fff" in bg or bg in ("#ffffff", "white", "rgb(255,255,255)"):
            return True
        if "#000" in bg or bg in ("#000000", "#111", "#222", "black"):
            return False
    for hint in color_hints[:5]:
        lower = hint.strip().lower()
        if "fff" in lower or lower in ("white", "rgb(255 255 255)"):
            return True
        if "000" in lower or lower in ("black", "#111"):
            return False
    return None


def _guess_border_radius(css: str) -> str:
    radii: list[float] = []
    for match in re.finditer(r'border-radius\s*:\s*([^;}{]+)', css, re.IGNORECASE):
        val = match.group(1).strip()
        m = re.search(r'(\d+(?:\.\d+)?)', val)
        if m:
            try:
                radii.append(float(m.group(1)))
            except ValueError:
                pass
    if not radii:
        return "small"
    avg = sum(radii) / len(radii)
    if avg == 0:
        return "none"
    if avg < 4:
        return "small"
    if avg < 12:
        return "medium"
    return "rounded"


def _guess_spacing(css: str) -> str:
    paddings: list[int] = []
    for match in re.finditer(r'padding\s*:\s*([^;}{]+)', css, re.IGNORECASE):
        val = match.group(1).strip()
        for m in re.finditer(r'(\d+)px', val):
            paddings.append(int(m.group(1)))
    if not paddings:
        return "normal"
    avg = sum(paddings) / len(paddings)
    if avg < 8:
        return "compact"
    if avg > 32:
        return "spacious"
    return "normal"


def _detect_cards_in_html(html_content: str) -> bool:
    keywords = ["card", "tile", "panel", "widget"]
    lower = html_content.lower()
    return any(kw in lower for kw in keywords)


def _detect_cards_in_css(css: str) -> bool:
    lower = css.lower()
    return "card" in lower or "box-shadow" in lower
