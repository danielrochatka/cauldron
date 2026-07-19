"""Tests for JSON schema validation of content items."""
from pathlib import Path

import pytest

from cauldron_cms_flatfile.parser import parse_content_file
from cauldron_cms_flatfile.validator import SchemaError, load_schema, validate_item
from cauldron_content.contracts import ContentItem, ContentStatus


def test_load_schema(parity_dir: Path):
    schema = load_schema(parity_dir / "schemas", "pages")
    assert schema["type"] == "object"


def test_missing_schema_raises(parity_dir: Path):
    with pytest.raises(SchemaError):
        load_schema(parity_dir / "schemas", "missing")


def test_valid_content_passes(parity_dir: Path):
    schema = load_schema(parity_dir / "schemas", "pages")
    item = parse_content_file(parity_dir / "pages" / "home.md", "pages", "flatfile")
    result = validate_item(item, schema)
    assert result.valid
    assert result.issues == ()


def test_invalid_content_fails(parity_dir: Path):
    schema = load_schema(parity_dir / "schemas", "pages")
    bad = ContentItem(
        id="page.x",
        collection="pages",
        slug="x",
        status=ContentStatus.PUBLISHED,
        schema="pages",
        data={},  # missing required title/description
        body="",
        hash="",
        provider="flatfile",
    )
    result = validate_item(bad, schema)
    assert not result.valid
    codes = {i.code for i in result.issues}
    assert "schema_validation_error" in codes
