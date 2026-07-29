"""Management command: convert a legacy output_root real directory to a symlink release.

Run this command once when upgrading from a version of cauldron-site-astro that used
a plain directory for output_root to the symlink-based atomic-activation design.

Operational requirement
-----------------------
This command MUST be run offline (before resuming traffic) on installations where
output_root is a real directory.  The conversion involves:

  1. Creating output_root.releases/<uuid>/ — a copy of the current output_root.
  2. Renaming output_root → output_root.releases/legacy-<uuid>/  (brief window).
  3. Creating output_root → symlink → new release directory (atomic from this point).

The brief window in step 2–3 means output_root is absent for a moment.  That is
acceptable during a planned maintenance window; it is NOT safe under live traffic.

After the command succeeds all subsequent builds use fully atomic symlink swaps.

Usage::

    manage.py cauldron_migrate_output_root [--output-root PATH]

If --output-root is omitted the path is read from the configured module settings.
"""
import shutil
import os
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


def _releases_dir(output_root: Path) -> Path:
    return output_root.parent / (output_root.name + ".releases")


def migrate_output_root(output_root: Path) -> Path:
    """Convert output_root from a real directory to a symlink pointing to a release.

    Returns the path of the release directory that output_root now points to.
    Raises ValueError if output_root is already a symlink (no migration needed).
    Raises FileNotFoundError if output_root does not exist at all.
    """
    if output_root.is_symlink():
        raise ValueError(
            f"{output_root} is already a symlink — no migration needed."
        )
    if not output_root.exists():
        raise FileNotFoundError(
            f"{output_root} does not exist — nothing to migrate."
        )
    if not output_root.is_dir():
        raise ValueError(
            f"{output_root} exists but is not a directory — cannot migrate."
        )

    releases = _releases_dir(output_root)
    releases.mkdir(parents=True, exist_ok=True)

    new_release = releases / uuid.uuid4().hex
    legacy = releases / ("legacy-" + uuid.uuid4().hex)
    next_link = output_root.parent / (output_root.name + ".next")

    # Step 1 — copy current content to the versioned release slot
    shutil.copytree(str(output_root), str(new_release))

    try:
        # Step 2 — move output_root aside (brief window starts here)
        output_root.rename(legacy)

        # Step 3 — point next_link at new release, then atomically rename into place
        next_link.unlink(missing_ok=True)
        next_link.symlink_to(new_release)
        os.rename(str(next_link), str(output_root))
        # Window ends — output_root is now a symlink to new_release

        # Step 4 — clean up the legacy copy now that new_release holds the canonical content
        shutil.rmtree(str(legacy), ignore_errors=True)

    except Exception:
        # Best-effort rollback: restore original directory if possible
        if legacy.exists() and not output_root.exists():
            try:
                legacy.rename(output_root)
            except Exception:
                pass
        shutil.rmtree(str(new_release), ignore_errors=True)
        next_link.unlink(missing_ok=True)
        raise

    return new_release


class Command(BaseCommand):
    help = (
        "Convert a legacy output_root real directory to the symlink-based release layout. "
        "Run offline before resuming traffic. Safe to skip if output_root is already a symlink."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-root",
            metavar="PATH",
            help=(
                "Path to output_root. Defaults to the path in module settings "
                "(cauldron.site.astro → output_root)."
            ),
        )

    def handle(self, *args, **options):
        path_str = options.get("output_root")

        if path_str:
            output_root = Path(path_str)
        else:
            from cauldron_site_astro.config import get_site_astro_config

            cfg = get_site_astro_config()
            if not cfg.output_root:
                raise CommandError(
                    "output_root is not configured. "
                    "Set cauldron.site.astro → output_root or pass --output-root."
                )
            output_root = Path(cfg.output_root)

        if output_root.is_symlink():
            self.stdout.write(
                self.style.SUCCESS(
                    f"{output_root} is already a symlink — no migration needed."
                )
            )
            return

        if not output_root.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    f"{output_root} does not exist yet — no migration needed."
                )
            )
            return

        self.stdout.write(f"Migrating {output_root} to symlink-based release layout…")

        try:
            release = migrate_output_root(output_root)
        except Exception as exc:
            raise CommandError(f"Migration failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Migration complete. {output_root} → {release}"
            )
        )
