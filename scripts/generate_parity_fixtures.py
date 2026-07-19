"""One-off script that regenerates the expected/*.json parity fixtures.

Run from the repository root:
    .venv/bin/python scripts/generate_parity_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

from cauldron_cms_flatfile.parser import parse_content_file

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "content-parity"
EXPECTED_DIR = FIXTURE_DIR / "expected"

MAPPINGS = [
    ("pages", "home.md", "home.expected.json"),
    ("pages", "about.md", "about.expected.json"),
    ("pages", "draft-page.md", "draft-page.expected.json"),
    ("posts", "first-post.md", "first-post.expected.json"),
]


def main() -> None:
    EXPECTED_DIR.mkdir(exist_ok=True)
    for collection, fname, outfile in MAPPINGS:
        src = FIXTURE_DIR / collection / fname
        item = parse_content_file(src, collection, "flatfile")
        expected = {
            "id": item.id,
            "collection": item.collection,
            "slug": item.slug,
            "status": item.status.value,
            "schema": item.schema,
            "data": item.data,
            "body": item.body,
            "hash": item.hash,
        }
        out_path = EXPECTED_DIR / outfile
        out_path.write_text(
            json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Generated {out_path}")


if __name__ == "__main__":
    main()
