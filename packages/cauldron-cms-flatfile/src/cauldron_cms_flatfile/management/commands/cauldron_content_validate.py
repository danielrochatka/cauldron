"""Validate all flat-file content against declared schemas."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from cauldron_cms_flatfile.config import FlatFileCMSConfig
from cauldron_cms_flatfile.repository import FlatFileRepository


class Command(BaseCommand):
    help = "Validate every content item in the configured flat-file site."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit output as JSON.",
        )
        parser.add_argument(
            "--include-drafts",
            action="store_true",
            help="Include drafts in the validation pass.",
        )

    def handle(self, *args, **opts) -> None:
        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        cfg = modules.get("cauldron.cms.flatfile") or {}
        site_root = cfg.get("site_root")
        if not site_root:
            self.stderr.write("cauldron.cms.flatfile.site_root is not configured.")
            sys.exit(2)
        config = FlatFileCMSConfig(
            site_root=Path(site_root),
            content_root=cfg.get("content_root", "content"),
            schema_root=cfg.get("schema_root", "schemas"),
        )
        repo = FlatFileRepository(config)

        report: list[dict] = []
        error_count = 0
        for collection in repo.list_collections():
            items = repo.list_items(collection, include_drafts=opts["include_drafts"])
            for item in items:
                result = repo.validate(item)
                entry: dict = {
                    "collection": collection,
                    "id": item.id,
                    "slug": item.slug,
                    "status": item.status.value,
                    "valid": result.valid,
                }
                if not result.valid:
                    error_count += 1
                    entry["issues"] = [
                        {
                            "code": i.code,
                            "message": i.message,
                            "json_path": i.json_path,
                        }
                        for i in result.issues
                    ]
                report.append(entry)

        if opts["as_json"]:
            self.stdout.write(json.dumps({"items": report, "errors": error_count}, indent=2))
        else:
            for entry in report:
                status = "OK" if entry["valid"] else "ERR"
                self.stdout.write(
                    f"[{status}] {entry['collection']}/{entry['id']} ({entry['slug']})"
                )
                if not entry["valid"]:
                    for issue in entry["issues"]:
                        self.stdout.write(f"    - {issue['code']}: {issue['message']}")
            self.stdout.write(f"Total errors: {error_count}")

        if error_count:
            sys.exit(1)
