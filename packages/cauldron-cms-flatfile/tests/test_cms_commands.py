"""Tests for management commands cauldron_content_validate and cauldron_content_list."""
import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test.utils import override_settings


def _run(command: str, **opts) -> str:
    out = StringIO()
    call_command(command, stdout=out, **opts)
    return out.getvalue()


def test_validate_all_ok(temp_site: Path):
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        output = _run("cauldron_content_validate")
    assert "Total errors: 0" in output


def test_validate_reports_errors(temp_site: Path):
    # Corrupt one file so its data fails schema validation
    bad = temp_site / "content" / "pages" / "broken.md"
    bad.write_text(
        "---\n"
        "id: page.broken\n"
        "slug: broken\n"
        "status: published\n"
        "schema: pages\n"
        "---\n\n"
        "No required title/description.\n",
        encoding="utf-8",
    )
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        with pytest.raises(SystemExit):
            _run("cauldron_content_validate")


def test_validate_json_output(temp_site: Path):
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        output = _run("cauldron_content_validate", as_json=True)
    payload = json.loads(output)
    assert payload["errors"] == 0
    assert payload["items"]


def test_list_default(temp_site: Path):
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        output = _run("cauldron_content_list")
    assert "page.home" in output
    assert "post.first" in output


def test_list_json(temp_site: Path):
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        output = _run("cauldron_content_list", as_json=True)
    payload = json.loads(output)
    ids = {row["id"] for row in payload["items"]}
    assert "page.home" in ids


def test_list_filtered_by_collection(temp_site: Path):
    with override_settings(
        CAULDRON_MODULES={"cauldron.cms.flatfile": {"site_root": str(temp_site)}}
    ):
        output = _run("cauldron_content_list", collection="pages", as_json=True)
    payload = json.loads(output)
    for row in payload["items"]:
        assert row["collection"] == "pages"
