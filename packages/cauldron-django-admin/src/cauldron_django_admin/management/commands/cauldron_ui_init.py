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
        """Validate the override directory by walking the raw tree.

        Uses ``os.walk`` rather than ``UIOverrideStore.list_files`` so we
        surface anything the store would *silently skip* — hidden files,
        symlinks, non-CSS files, entries above the scope directories,
        oversize files, invalid UTF-8, and roots that exceed the total-size
        budget.
        """
        import os
        from cauldron_django_admin.override_store import (
            MAX_FILE_BYTES, MAX_TOTAL_BYTES,
        )

        issues: list[str] = []

        if not root.exists():
            msg = "Override root does not exist. Run cauldron_ui_init to create it."
            self.stderr.write(self.style.WARNING(f"  [WARN] {msg}"))
            raise CommandError(msg)
        if not root.is_dir():
            msg = "Override root path exists but is not a directory."
            self.stderr.write(self.style.WARNING(f"  [WARN] {msg}"))
            raise CommandError(msg)

        # Allowed root-level entries. Everything else is reported.
        allowed_root_names = frozenset({
            "admin", "pages", ".gitignore", ".cauldron-store.lock",
        })

        total_bytes = 0

        for entry in sorted(root.iterdir()):
            name = entry.name
            if entry.is_symlink():
                try:
                    link_target = entry.resolve()
                    link_target.relative_to(root)
                except (ValueError, OSError):
                    issues.append(f"Symlink escape at root level: {name!r}")
                else:
                    issues.append(f"Symlink at root level: {name!r}")
                continue

            if name not in allowed_root_names:
                if name.startswith("."):
                    issues.append(f"Hidden file at root level: {name!r}")
                else:
                    issues.append(f"Unexpected root-level entry: {name!r}")
                continue

            if name not in ("admin", "pages"):
                # .gitignore and .cauldron-store.lock are fine.
                continue

            scope_dir = entry
            if not scope_dir.is_dir():
                issues.append(f"Scope entry is not a directory: {name!r}")
                continue

            for dirpath, dirnames, filenames in os.walk(
                str(scope_dir), topdown=True, followlinks=False,
            ):
                dp = Path(dirpath)
                rel_dir = dp.relative_to(root)

                # Hidden directories are skipped from recursion but reported
                # so operators know why files inside them are not visible.
                hidden_dirs = [d for d in dirnames if d.startswith(".")]
                for hd in hidden_dirs:
                    issues.append(f"Hidden directory: {rel_dir / hd}")
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]

                for fname in sorted(filenames):
                    fpath = dp / fname
                    rel_path = fpath.relative_to(root)

                    if fname.startswith("."):
                        issues.append(f"Hidden file: {rel_path}")
                        continue

                    if fpath.is_symlink():
                        try:
                            target = fpath.resolve()
                            target.relative_to(root)
                        except (ValueError, OSError):
                            issues.append(f"Symlink escape: {rel_path}")
                        else:
                            issues.append(f"Symlink in override tree: {rel_path}")
                        continue

                    if fpath.suffix.lower() != ".css":
                        issues.append(f"Non-CSS file: {rel_path}")
                        continue

                    try:
                        raw = fpath.read_bytes()
                    except OSError as exc:
                        issues.append(
                            f"Cannot read file: {rel_path}: {type(exc).__name__}",
                        )
                        continue

                    file_size = len(raw)
                    if file_size > MAX_FILE_BYTES:
                        issues.append(
                            f"File exceeds per-file limit ({file_size} bytes): {rel_path}"
                        )

                    try:
                        raw.decode("utf-8")
                    except UnicodeDecodeError:
                        issues.append(f"Invalid UTF-8: {rel_path}")

                    total_bytes += file_size

        for scope in ("admin", "pages"):
            if not (root / scope).is_dir():
                issues.append(f"Missing scope directory: {scope}/")

        if total_bytes > MAX_TOTAL_BYTES:
            issues.append(
                f"Total override root size ({total_bytes} bytes) exceeds "
                f"limit ({MAX_TOTAL_BYTES} bytes).",
            )

        if issues:
            for issue in issues:
                self.stderr.write(self.style.WARNING(f"  [WARN] {issue}"))
            # Raise CommandError so --check exits non-zero and CI can gate
            # on it.
            raise CommandError(
                "Override directory has validation issues. Run cauldron_ui_init to fix."
            )
        self.stdout.write(self.style.SUCCESS("Override directory is valid."))
