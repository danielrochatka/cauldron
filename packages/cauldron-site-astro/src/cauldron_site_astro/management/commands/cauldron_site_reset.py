"""Management command to reset Cauldron public site content, styles, or both."""
from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reset site content, styles, or both, then rebuild the public site."

    def add_arguments(self, parser):
        parser.add_argument(
            "--content",
            action="store_true",
            help="Delete all content items.",
        )
        parser.add_argument(
            "--styles",
            action="store_true",
            help="Clear active and staged CSS.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all",
            help="Delete all content and clear styles (supersedes --content/--styles).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options):
        do_all = options["all"]
        do_content = options["content"] or do_all
        do_styles = options["styles"] or do_all

        # No scope flags → reset everything.
        if not do_content and not do_styles:
            do_content = True
            do_styles = True

        if not options["yes"]:
            if not self._confirm(do_content, do_styles):
                self.stdout.write("Aborted.")
                return

        # Resolve every required service before any mutation so a broken
        # configuration aborts cleanly rather than after partial deletion.
        build_svc, router, theme_svc = self._resolve_services(do_content, do_styles)

        # Hard failure: content deletion must succeed before styles are touched.
        removed = 0
        if do_content:
            removed = self._reset_content(router)  # raises SystemExit(1) on failure

        # Snapshot prior style state so it can be restored if the rebuild fails.
        style_snapshot = None
        if do_styles and theme_svc is not None:
            style_snapshot = self._snapshot_styles(theme_svc)
            self._apply_style_reset(theme_svc)

        # Rebuild; restore styles on failure (content rollback is out of scope).
        result = build_svc.build()
        if not result.ok:
            if do_styles and theme_svc is not None and style_snapshot is not None:
                self._restore_styles(theme_svc, style_snapshot)
            self.stderr.write(self.style.ERROR(f"Site build failed: {result.error}"))
            raise SystemExit(1)

        # Success summary — only mention selected scopes.
        parts: list[str] = []
        if do_content:
            parts.append(f"{removed} content item(s) removed")
        if do_styles:
            parts.append("styles reset")
        parts.append("public site rebuilt")
        self.stdout.write(
            self.style.SUCCESS(f"Website reset complete: {', '.join(parts)}.")
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        return answer in ("y", "yes")

    def _resolve_services(self, do_content: bool, do_styles: bool):
        """Resolve all required services before mutation begins.

        Raises if any required service cannot be initialised so that no
        scope is mutated when the configuration is broken.
        """
        from cauldron_site_astro.config import get_site_astro_config
        from cauldron_site_astro.service import get_build_service
        from cauldron_site_astro.theme import SiteThemeService

        build_svc = get_build_service()
        router = build_svc._router if do_content else None

        theme_svc = None
        if do_styles:
            cfg = get_site_astro_config()
            if cfg.theme_root:
                theme_svc = SiteThemeService(cfg.theme_root)

        return build_svc, router, theme_svc

    def _reset_content(self, router) -> int:
        """Delete every content item via the router's contract.

        Uses strict enumeration so that a provider that cannot enumerate its
        collections causes an immediate abort rather than a silent partial reset.
        Uses each item's enumerated hash for optimistic concurrency so that a
        concurrent edit between enumeration and apply causes a conflict rather
        than being silently overwritten.

        Raises SystemExit(1) if enumeration fails (zero mutations committed —
        strict enumeration aborts before any apply) or if router.apply() does
        not succeed (command aborts before styles, build, or success reporting).
        With multiple providers, an earlier provider may have applied before a
        later one fails; cross-provider rollback is outside this command's
        contract.
        """
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
        )
        from cauldron_content.router import RouterError

        try:
            collections = router.list_collections(strict=True)
        except RouterError as exc:
            self.stderr.write(
                self.style.ERROR(
                    f"Cannot enumerate all content providers; reset aborted "
                    f"before any mutation.\n{exc}"
                )
            )
            raise SystemExit(1)

        operations: list[ContentOperation] = []
        for coll_info in collections:
            items = router.list_items(coll_info.name, include_drafts=True)
            for item in items:
                operations.append(
                    ContentOperation(
                        kind=ContentOperationKind.DELETE,
                        provider=item.provider,
                        collection=item.collection,
                        item_id=item.id,
                        expected_hash=item.hash,
                        force=False,
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
            lines: list[str] = []
            for c in list(result.conflicts)[:5]:
                lines.append(f"  conflict: {c.item_id!r} ({c.collection}): {c.message}")
            for e in list(result.validation_errors)[:5]:
                lines.append(f"  validation error: {e.item_id!r}: {e.message}")
            detail = "\n".join(lines)
            msg = (
                f"Content deletion failed: {len(result.conflicts)} conflict(s), "
                f"{len(result.validation_errors)} validation error(s)."
            )
            if detail:
                msg = f"{msg}\n{detail}"
            self.stderr.write(self.style.ERROR(msg))
            raise SystemExit(1)

        return len(operations)

    def _snapshot_styles(self, theme_svc) -> dict:
        return {
            "active": theme_svc.get_active_css(),
            "staged": theme_svc.get_staged_css(),
        }

    def _apply_style_reset(self, theme_svc) -> None:
        theme_svc.set_active_css("")
        theme_svc.discard_staged()

    def _restore_styles(self, theme_svc, snapshot: dict) -> None:
        theme_svc.set_active_css(snapshot["active"])
        staged = snapshot["staged"]
        if staged is not None:
            theme_svc.stage_css(staged)
