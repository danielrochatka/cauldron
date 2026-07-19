"""Tests for the flat-file Markdown parser."""
from pathlib import Path

import pytest

from cauldron_cms_flatfile.parser import ParseError, parse_content_file
from cauldron_content.contracts import ContentStatus


def test_parse_valid_page(parity_dir: Path):
    item = parse_content_file(parity_dir / "pages" / "home.md", "pages", "flatfile")
    assert item.id == "page.home"
    assert item.slug == "home"
    assert item.status == ContentStatus.PUBLISHED
    assert item.schema == "pages"
    assert item.data == {"title": "Home", "description": "Welcome to the home page."}
    assert "Welcome to Cauldron" in item.body
    assert item.hash


def test_parse_draft(parity_dir: Path):
    item = parse_content_file(parity_dir / "pages" / "draft-page.md", "pages", "flatfile")
    assert item.status == ContentStatus.DRAFT


def test_reserved_fields_not_in_data(parity_dir: Path):
    item = parse_content_file(parity_dir / "pages" / "home.md", "pages", "flatfile")
    for reserved in ("id", "slug", "status", "schema"):
        assert reserved not in item.data


def test_missing_id_raises(parity_dir: Path):
    with pytest.raises(ParseError):
        parse_content_file(parity_dir / "invalid" / "missing-id.md", "invalid", "flatfile")


def test_invalid_status_raises(parity_dir: Path):
    with pytest.raises(ParseError):
        parse_content_file(
            parity_dir / "invalid" / "invalid-status.md", "invalid", "flatfile"
        )


def test_post_with_list_data(parity_dir: Path):
    item = parse_content_file(
        parity_dir / "posts" / "first-post.md", "posts", "flatfile"
    )
    assert item.data["tags"] == ["hello", "world"]


def test_body_is_normalized(parity_dir: Path):
    item = parse_content_file(parity_dir / "pages" / "home.md", "pages", "flatfile")
    # Ends with LF, single trailing newline
    assert item.body.endswith("\n")
    assert "\r" not in item.body
