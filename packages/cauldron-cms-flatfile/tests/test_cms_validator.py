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


# ---------------------------------------------------------------------------
# Page schema (page.schema.json)
# ---------------------------------------------------------------------------

def test_page_schema_loads(tmp_path):
    """The page.schema.json from cauldron-app/schemas/ can be loaded."""
    import json
    import shutil
    from pathlib import Path
    # Locate the real page schema from the cauldron-app
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    assert schema["type"] == "object"
    assert "title" in schema.get("required", [])


def test_page_schema_validates_minimal(tmp_path):
    """Minimal page data (title only) passes the page schema."""
    import json
    import shutil
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    item = ContentItem(
        id="pg-1",
        collection="pages",
        slug="about",
        status=ContentStatus.DRAFT,
        schema="page",
        data={"title": "About Us"},
        body="# About",
        hash="",
        provider="flatfile",
    )
    result = validate_item(item, schema)
    assert result.valid, [i.message for i in result.issues]


def test_page_schema_validates_complete(tmp_path):
    """Complete page data passes the page schema."""
    import shutil
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    item = ContentItem(
        id="pg-2",
        collection="pages",
        slug="about-us",
        status=ContentStatus.PUBLISHED,
        schema="page",
        data={
            "title": "About Us",
            "navigation_title": "About",
            "summary": "Our story.",
            "seo_title": "About Our Organization",
            "meta_description": "Learn more about us.",
            "canonical_url": "https://example.com/about-us",
            "robots_index": True,
            "robots_follow": True,
            "social_title": "About",
            "social_description": "Share text",
            "social_image": "https://example.com/img.png",
            "template": "page",
        },
        body="# About\n\nContent.",
        hash="",
        provider="flatfile",
    )
    result = validate_item(item, schema)
    assert result.valid, [i.message for i in result.issues]


def test_page_schema_requires_title(tmp_path):
    """Page data missing title fails validation."""
    import shutil
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    item = ContentItem(
        id="pg-3",
        collection="pages",
        slug="no-title",
        status=ContentStatus.DRAFT,
        schema="page",
        data={},
        body="",
        hash="",
        provider="flatfile",
    )
    result = validate_item(item, schema)
    assert not result.valid


def test_page_schema_rejects_unknown_property(tmp_path):
    """Page data with additionalProperties=false rejects unknown fields."""
    import shutil
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    item = ContentItem(
        id="pg-4",
        collection="pages",
        slug="extra",
        status=ContentStatus.DRAFT,
        schema="page",
        data={"title": "Test", "unknown_field": "value"},
        body="",
        hash="",
        provider="flatfile",
    )
    result = validate_item(item, schema)
    assert not result.valid


def test_page_schema_rejects_overlong_title(tmp_path):
    """Title exceeding 200 chars fails validation."""
    import shutil
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    page_schema_src = repo_root / "cauldron-app" / "schemas" / "page.schema.json"
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    shutil.copy2(page_schema_src, schema_dir / "page.schema.json")
    schema = load_schema(schema_dir, "page")
    item = ContentItem(
        id="pg-5",
        collection="pages",
        slug="long",
        status=ContentStatus.DRAFT,
        schema="page",
        data={"title": "T" * 201},
        body="",
        hash="",
        provider="flatfile",
    )
    result = validate_item(item, schema)
    assert not result.valid
