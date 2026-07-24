"""Management command: cauldron_ui_init — initialize the CSS override directory."""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


_DEFAULT_ADMIN_FILES = {
    "00-variables.css": "/* Site variable overrides — loaded first */\n",
    "10-layout.css": "/* Site layout overrides */\n",
    "20-components.css": "/* Site component overrides */\n",
    "90-site.css": "/* Site-wide admin CSS customizations */\n",
}

_DEFAULT_PAGES_FILES = {
    "00-variables.css": "/* Site variable overrides for public pages */\n",
    "90-site.css": "/* Site-wide public page CSS customizations */\n",
}

_GITIGNORE_CONTENT = "*\n!.gitignore\n"


class Command(BaseCommand):
    help = "Initialize the Cauldron UI override directory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace existing files (use with caution).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the override directory without creating files.",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
        if override_dir is None:
            base_dir = getattr(settings, "BASE_DIR", None)
            if base_dir is None:
                self.stderr.write(
                    self.style.ERROR(
                        "BASE_DIR is not set and CAULDRON_UI_OVERRIDES_DIR is not configured."
                    )
                )
                return
            root = Path(base_dir) / "cauldron-overrides"
        else:
            root = Path(override_dir)

        if options["check"]:
            self._run_check(root)
            return

        force = options["force"]
        self._create_dir(root, force)
        self._create_dir(root / "admin", force)
        self._create_dir(root / "pages", force)
        self._write_file(root / ".gitignore", _GITIGNORE_CONTENT, force)
        for name, content in _DEFAULT_ADMIN_FILES.items():
            self._write_file(root / "admin" / name, content, force)
        for name, content in _DEFAULT_PAGES_FILES.items():
            self._write_file(root / "pages" / name, content, force)

        self.stdout.write(self.style.SUCCESS("Cauldron UI override directory initialized."))
        self.stdout.write("  Location: [override root configured in settings]")
        self.stdout.write("  Scopes: admin, pages")

    def _create_dir(self, path: Path, force: bool) -> None:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f"  Created directory: {path.name}/")
        else:
            self.stdout.write(f"  Exists: {path.name}/")

    def _write_file(self, path: Path, content: str, force: bool) -> None:
        if path.exists() and not force:
            self.stdout.write(f"  Skipped (exists): {path.name}")
            return
        path.write_text(content, encoding="utf-8")
        self.stdout.write(f"  Written: {path.name}")

    def _run_check(self, root: Path) -> None:
        """Validate the override directory using ``validate_override_tree``."""
        from cauldron_django_admin.override_store import validate_override_tree

        if not root.exists():
            msg = "Override root does not exist. Run cauldron_ui_init to create it."
            self.stderr.write(self.style.WARNING(f"  [WARN] {msg}"))
            raise CommandError(msg)

        issues = validate_override_tree(root)

        if issues:
            for issue in issues:
                self.stderr.write(self.style.WARNING(f"  [WARN] {issue}"))
            raise CommandError(
                "Override directory has validation issues. Run cauldron_ui_init to fix."
            )
        self.stdout.write(self.style.SUCCESS("Override directory is valid."))
