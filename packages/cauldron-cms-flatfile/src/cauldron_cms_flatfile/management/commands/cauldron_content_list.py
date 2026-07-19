"""List content items known to the flat-file provider."""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from cauldron_cms_flatfile.config import FlatFileCMSConfig
from cauldron_cms_flatfile.repository import FlatFileRepository


class Command(BaseCommand):
    help = "List content items from the flat-file CMS."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--collection", type=str, default="")
        parser.add_argument("--json", dest="as_json", action="store_true")
        parser.add_argument("--include-drafts", action="store_true")

    def handle(self, *args, **opts) -> None:
        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        cfg = modules.get("cauldron.cms.flatfile") or {}
        site_root = cfg.get("site_root")
        if not site_root:
            self.stderr.write("cauldron.cms.flatfile.site_root is not configured.")
            return
        config = FlatFileCMSConfig(
            site_root=Path(site_root),
            content_root=cfg.get("content_root", "content"),
            schema_root=cfg.get("schema_root", "schemas"),
        )
        repo = FlatFileRepository(config)

        collections = [opts["collection"]] if opts["collection"] else repo.list_collections()
        rows: list[dict] = []
        for coll in collections:
            for item in repo.list_items(coll, include_drafts=opts["include_drafts"]):
                rows.append(
                    {
                        "collection": coll,
                        "id": item.id,
                        "slug": item.slug,
                        "status": item.status.value,
                        "schema": item.schema,
                        "hash": item.hash,
                    }
                )

        if opts["as_json"]:
            self.stdout.write(json.dumps({"items": rows}, indent=2))
        else:
            for row in rows:
                self.stdout.write(
                    f"{row['collection']:<12} {row['status']:<10} "
                    f"{row['id']:<24} {row['slug']}"
                )
            self.stdout.write(f"Total items: {len(rows)}")
