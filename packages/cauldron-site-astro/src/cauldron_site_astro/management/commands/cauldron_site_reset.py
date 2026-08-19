"""Management command to reset Cauldron public site content, styles, or both."""
from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reset site content, styles, or both, then rebuild the public site."

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument(
            "--content",
            action="store_true",
            help="Delete all content items only.",
        )
        scope.add_argument(
            "--styles",
            action="store_true",
            help="Clear active and staged CSS only.",
        )
        scope.add_argument(
            "--all",
            action="store_true",
            dest="all",
            help="Delete all content and clear styles (default when no flag given).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options):
        do_content = options["content"]
        do_styles = options["styles"]
        do_all = options["all"]

        if not do_content and not do_styles and not do_all:
            do_all = True

        if do_all:
            do_content = True
            do_styles = True

        if not options["yes"]:
            if not self._confirm(do_content, do_styles):
                self.stdout.write("Aborted.")
                return

        removed = 0
        if do_content:
            removed = self._reset_content()

        if do_styles:
            self._reset_styles()

        self._rebuild()

        parts: list[str] = [f"{removed} content item(s) removed"]
        if do_styles:
            parts.append("styles reset")
        parts.append("public site rebuilt")
        self.stdout.write(
            self.style.SUCCESS(f"Website reset complete: {', '.join(parts)}.")
        )

    def _confirm(self, do_content: bool, do_styles: bool) -> bool:
        scope_parts: list[str] = []
        if do_content:
            scope_parts.append("all content")
        if do_styles:
            scope_parts.append("all styles")
        scope_desc = " and ".join(scope_parts)
        self.stdout.write(
            f"This will permanently delete {scope_desc} and rebuild the site."
        )
        answer = input("Continue? [y/N]: ").strip().lower()
        return answer == "y"

    def _reset_content(self) -> int:
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
        )
        from cauldron_site_astro.service import get_build_service

        svc = get_build_service()
        router = svc._router

        operations: list[ContentOperation] = []
        for coll_info in router.list_collections():
            items = router.list_items(coll_info.name, include_drafts=True)
            for item in items:
                operations.append(
                    ContentOperation(
                        kind=ContentOperationKind.DELETE,
                        provider=item.provider,
                        collection=item.collection,
                        item_id=item.id,
                        force=True,
                    )
                )

        if not operations:
            return 0

        changeset = ContentChangeSet(
            id=str(uuid.uuid4()),
            operations=tuple(operations),
            author="cauldron_site_reset",
            description="Site reset: delete all content.",
        )
        result = router.apply(changeset)
        if not result.success:
            self.stderr.write(
                self.style.WARNING(
                    "Some content items could not be deleted; see errors above."
                )
            )
        return len(operations)

    def _reset_styles(self) -> None:
        from cauldron_site_astro.config import get_site_astro_config
        from cauldron_site_astro.theme import SiteThemeService

        cfg = get_site_astro_config()
        if not cfg.theme_root:
            return
        theme_svc = SiteThemeService(cfg.theme_root)
        theme_svc.set_active_css("")
        theme_svc.discard_staged()

    def _rebuild(self) -> None:
        from cauldron_site_astro.service import get_build_service

        result = get_build_service().build()
        if not result.ok:
            self.stderr.write(self.style.ERROR(f"Site build failed: {result.error}"))
            raise SystemExit(1)
