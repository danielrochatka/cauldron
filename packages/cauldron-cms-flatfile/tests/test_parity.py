"""Parity tests: parser output must match expected.*.json fixtures byte-for-byte."""
import json
from pathlib import Path

import pytest

from cauldron_cms_flatfile.parser import parse_content_file

PARITY_CASES = [
    ("pages", "home.md", "home.expected.json"),
    ("pages", "about.md", "about.expected.json"),
    ("pages", "draft-page.md", "draft-page.expected.json"),
    ("posts", "first-post.md", "first-post.expected.json"),
]


@pytest.mark.parametrize("collection,src,expected", PARITY_CASES)
def test_parity(collection: str, src: str, expected: str, parity_dir: Path):
    item = parse_content_file(parity_dir / collection / src, collection, "flatfile")
    expected_data = json.loads(
        (parity_dir / "expected" / expected).read_text(encoding="utf-8")
    )
    assert item.id == expected_data["id"]
    assert item.collection == expected_data["collection"]
    assert item.slug == expected_data["slug"]
    assert item.status.value == expected_data["status"]
    assert item.schema == expected_data["schema"]
    assert item.data == expected_data["data"]
    assert item.body == expected_data["body"]
    assert item.hash == expected_data["hash"], (
        f"Hash mismatch for {src}: {item.hash} != {expected_data['hash']}"
    )
