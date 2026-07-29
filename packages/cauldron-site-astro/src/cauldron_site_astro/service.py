"""Site build service for cauldron-site-astro."""
from __future__ import annotations

import fcntl
import glob
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Django -> Astro manifest contract
# ---------------------------------------------------------------------------
#
# ``MANIFEST_API_VERSION`` documents the schema the Astro side reads. Any
# breaking change to the manifest (removed field, renamed key, new required
# field) MUST bump this string. The contract test in ``tests/test_contract.py``
# pins the expected shape so drift is caught early.
#
# Consumers on the Astro side must import from ``src/lib/manifest.ts`` and
# never touch ``process.env.CAULDRON_MANIFEST`` directly.
MANIFEST_API_VERSION = "1.0"


@dataclass
class BuildResult:
    ok: bool
    pages_built: int = 0
    output_dir: str = ""
    error: str = ""
    build_log: str = ""


def _promote_output(src_dir: Path | str, output_root: Path) -> None:
    """Atomically replace output_root with src_dir using a locked staging rename.

    Steps:
      1. Acquire an exclusive flock on output_root + ".swap.lock"
      2. Copy src_dir → staging path  (not yet live)
      3. Rename output_root → previous path  (if output_root exists)
      4. Rename staging → output_root  (atomic on same filesystem)
      5. Remove previous path

    The live output_root transitions directly from fully-old to fully-new in
    step 4; no partially-copied tree is ever exposed.

    On any exception in steps 2–5:
      - Restore previous → output_root if output_root is missing
      - Remove staging and previous (ignore errors)
      - Re-raise
    """
    src_dir = Path(src_dir)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    lock_path = Path(str(output_root) + ".swap.lock")
    staging = Path(str(output_root) + ".staging-" + uuid.uuid4().hex[:8])
    previous = Path(str(output_root) + ".previous-" + uuid.uuid4().hex[:8])

    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            shutil.copytree(str(src_dir), str(staging))

            if output_root.exists():
                output_root.rename(previous)

            staging.rename(output_root)

            if previous.exists():
                shutil.rmtree(previous)

        except Exception:
            if previous.exists() and not output_root.exists():
                try:
                    previous.rename(output_root)
                except Exception:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(previous, ignore_errors=True)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _promote_output_snapshotted(src_dir: Path | str, output_root: Path) -> "Path | None":
    """Like _promote_output but retains the displaced directory as a rollback snapshot.

    Exact filesystem operations
    ----------------------------
    1. Acquire an exclusive flock on ``output_root.swap.lock`` (serialises
       concurrent publish attempts).
    2. ``shutil.copytree(src_dir, output_root.staging-<uuid>)`` — the full
       copy lands in a private path that is *never served*.  A concurrent
       reader of ``output_root`` observes nothing during this step.
    3. ``os.rename(output_root, output_root.previous-<uuid>)`` — displaces
       the current live tree in one atomic rename(2) syscall.  ``output_root``
       ceases to exist for an instant, but the webserver's open file handles
       on files within it remain valid until all handles are closed (POSIX
       unlink semantics).
    4. ``os.rename(staging, output_root)`` — makes the new complete tree live
       in one atomic rename(2) syscall.  From this moment a reader either saw
       the old ``output_root`` (still valid via open handles) or sees the new
       complete one.  No partial tree is ever reachable at ``output_root``.

    Step 5 of ``_promote_output`` (``rmtree(previous)``) is intentionally
    **skipped**; the displaced directory is kept as a rollback snapshot and its
    path is returned to the caller.

    Returns the Path of the snapshot, or None if output_root did not exist
    (first-ever publish — nothing to snapshot).

    Same-filesystem constraint
    --------------------------
    ``staging`` and ``previous`` are both created under ``output_root.parent``
    (e.g. ``/srv/site.staging-a1b2c3d4``).  Because ``output_root``,
    ``staging``, and ``previous`` all share the same parent directory they are
    guaranteed to be on the same filesystem.  ``os.rename()`` across
    filesystems raises ``OSError(EXDEV)``; that error is impossible here
    provided ``output_root`` and its parent directory are on the same mount.

    Reader-visible states at ``output_root``
    -----------------------------------------
    At any observable instant ``output_root`` is in one of these states:
    - Does not exist yet (before any publish).
    - Points to the previous complete tree (before step 4).
    - Points to the new complete tree (after step 4).
    A reader never observes a partially-copied tree or the backup path as
    the live root.

    The caller MUST subsequently call either:
      - ``restore_output(snapshot)``       — to roll back (calls _promote_output + rmtree)
      - ``discard_output_backup(snapshot)`` — to commit (rmtree only)
    """
    src_dir = Path(src_dir)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    lock_path = Path(str(output_root) + ".swap.lock")
    staging = Path(str(output_root) + ".staging-" + uuid.uuid4().hex[:8])
    previous = Path(str(output_root) + ".previous-" + uuid.uuid4().hex[:8])
    had_previous = False

    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            shutil.copytree(str(src_dir), str(staging))

            if output_root.exists():
                output_root.rename(previous)
                had_previous = True

            staging.rename(output_root)

            # Do NOT remove previous — caller holds it as a rollback snapshot.
            return previous if had_previous else None

        except Exception:
            if previous.exists() and not output_root.exists():
                try:
                    previous.rename(output_root)
                except Exception:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(previous, ignore_errors=True)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


class SiteBuildService:
    """Builds the Cauldron public site from published content using Astro."""

    def __init__(self, config, router):
        self._config = config
        self._router = router  # ContentRouter or duck-typed object with list_items()

    def build(self, *, theme_css_override: str = "") -> BuildResult:
        """Build the full public site.

        1. Collect published pages from router.
        2. Write JSON manifest to a temporary file.
        3. Run ``npm run build`` in the frontend_root with CAULDRON_MANIFEST
           and CAULDRON_OUTDIR env vars.
        4. On success: atomically replace output_root with the temp output directory.
        5. On failure: leave output_root untouched.
        6. Clean up temp files regardless.

        ``theme_css_override`` — when non-empty, use this string as the
        published theme instead of reading ``active.css`` from theme_root.
        This is how the publish workflow injects the change set's staged CSS
        into the build **before** promoting it, so a failed build never
        touches ``active.css``.
        """
        import json
        import logging
        import os
        import subprocess
        import tempfile

        from cauldron_content.homepage import (
            HOMEPAGE_COLLECTION,
            HOMEPAGE_ITEM_ID,
            HOMEPAGE_ROUTE,
        )

        logger = logging.getLogger(__name__)
        cfg = self._config

        if not cfg.frontend_root or not cfg.output_root:
            return BuildResult(
                ok=False,
                error=(
                    "cauldron.site.astro frontend_root and output_root must be configured."
                ),
            )

        frontend_root = Path(cfg.frontend_root)
        output_root = Path(cfg.output_root)

        # Clean up any abandoned staging/previous paths from crashed prior runs
        for pattern in (
            str(output_root) + ".staging-*",
            str(output_root) + ".previous-*",
        ):
            for abandoned in glob.glob(pattern):
                shutil.rmtree(abandoned, ignore_errors=True)

        # Collect published pages
        pages = []
        try:
            items = self._router.list_items(HOMEPAGE_COLLECTION, include_drafts=False)
        except Exception as exc:
            return BuildResult(ok=False, error=f"Failed to list pages: {exc}")

        for item in items:
            if item.status != "published":
                continue
            if item.id == HOMEPAGE_ITEM_ID:
                route = HOMEPAGE_ROUTE
            else:
                route = f"/{item.slug}/"
            pages.append(
                {
                    "id": item.id,
                    "route": route,
                    "title": item.data.get("title", ""),
                    "navigation_title": item.data.get("navigation_title", ""),
                    "summary": item.data.get("summary", ""),
                    "body": item.body or "",
                    "template": item.data.get("template", "page"),
                    "seo_title": item.data.get("seo_title", ""),
                    "meta_description": item.data.get("meta_description", ""),
                    "canonical_url": item.data.get("canonical_url", ""),
                    "robots_index": item.data.get("robots_index", True),
                    "robots_follow": item.data.get("robots_follow", True),
                    "social_title": item.data.get("social_title", ""),
                    "social_description": item.data.get("social_description", ""),
                    "social_image": item.data.get("social_image", ""),
                }
            )

        if not pages:
            logger.info("No published pages; replacing output with empty directory.")
            empty_dir = None
            try:
                empty_dir = Path(tempfile.mkdtemp(prefix="cauldron_astro_empty_"))
                _promote_output(empty_dir, output_root)
            except Exception as exc:
                return BuildResult(ok=False, error=str(exc))
            finally:
                if empty_dir and empty_dir.exists():
                    shutil.rmtree(empty_dir, ignore_errors=True)
            return BuildResult(ok=True, pages_built=0, output_dir=str(output_root))

        # Determine effective theme CSS: an explicit override wins (used by
        # the publish workflow to build with the staged CSS *before* it is
        # promoted). Otherwise fall back to active.css from theme_root.
        theme_css = theme_css_override
        if not theme_css and cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_svc = SiteThemeService(cfg.theme_root)
                theme_css = theme_svc.get_active_css()
            except Exception:
                pass  # Non-fatal: build without theme

        manifest = {
            "api_version": MANIFEST_API_VERSION,
            "pages": pages,
            "theme": {"css_content": theme_css},
        }
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="cauldron_astro_")
            manifest_path = os.path.join(tmp_dir, "manifest.json")
            tmp_out = os.path.join(tmp_dir, "out")

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            env = {
                **os.environ,
                "CAULDRON_MANIFEST": manifest_path,
                "CAULDRON_OUTDIR": tmp_out,
            }
            proc = subprocess.run(
                [cfg.npm_command, "run", "build"],
                cwd=str(frontend_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=cfg.build_timeout,
            )
            build_log = (proc.stdout or "") + (proc.stderr or "")

            if proc.returncode != 0:
                logger.error(
                    "Astro build failed (exit %d):\n%s",
                    proc.returncode,
                    build_log[-2000:],
                )
                return BuildResult(
                    ok=False,
                    error=f"Astro build exited {proc.returncode}.",
                    build_log=build_log,
                )

            # Atomic replace: promote tmp_out over output_root
            _promote_output(tmp_out, output_root)

            return BuildResult(
                ok=True,
                pages_built=len(pages),
                output_dir=str(output_root),
                build_log=build_log,
            )

        except subprocess.TimeoutExpired:
            return BuildResult(
                ok=False,
                error=f"Astro build timed out after {cfg.build_timeout}s.",
            )
        except Exception as exc:
            return BuildResult(ok=False, error=str(exc))
        finally:
            if tmp_dir and Path(tmp_dir).exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)


    def promote_output(self, src_dir: "str | Path") -> None:
        """Atomically replace the live output_root with src_dir (no rollback snapshot)."""
        if not self._config.output_root:
            raise ValueError(
                "cauldron.site.astro output_root must be configured."
            )
        _promote_output(src_dir, Path(self._config.output_root))

    def promote_output_with_backup(self, src_dir: "str | Path") -> "Path | None":
        """Atomically replace output_root with src_dir, keeping the previous as a snapshot.

        Returns the Path of the kept-aside previous directory, or None if
        output_root did not exist yet (first publish).

        The caller MUST eventually call either:
          - ``restore_output(snapshot)``       — on failure (rolls back + discards)
          - ``discard_output_backup(snapshot)`` — on success (discards the old copy)
        """
        if not self._config.output_root:
            raise ValueError(
                "cauldron.site.astro output_root must be configured."
            )
        return _promote_output_snapshotted(src_dir, Path(self._config.output_root))

    def restore_output(self, snapshot: "Path | None") -> None:
        """Replace output_root with the snapshot from promote_output_with_backup.

        Silently no-ops if snapshot is None or has already been removed.
        After this call the snapshot path no longer exists — the caller
        should set their reference to None to prevent double-cleanup.
        """
        if snapshot is None:
            return
        snapshot = Path(snapshot)
        if not snapshot.exists():
            return
        if not self._config.output_root:
            return
        # _promote_output copies snapshot → staging, atomically swaps into output_root,
        # and discards whatever was currently in output_root.
        _promote_output(snapshot, Path(self._config.output_root))
        shutil.rmtree(str(snapshot), ignore_errors=True)

    def discard_output_backup(self, snapshot: "Path | None") -> None:
        """Discard the snapshot after a successful publish (commit path)."""
        if snapshot is not None:
            shutil.rmtree(str(snapshot), ignore_errors=True)

    def build_preview(
        self,
        *,
        output_dir: "str | Path",
        extra_items: "list | None" = None,
        item_ids_to_include: "list[str] | None" = None,
        theme_css: str = "",
    ) -> BuildResult:
        """Build a preview scoped to a specific set of drafts + a proposed theme.

        Baseline: all currently *published* items in the homepage collection.

        ``item_ids_to_include`` — draft items whose ids appear here are added
        to the preview (overriding the published version if the ids collide).
        Drafts NOT listed here are excluded, so an in-flight preview cannot
        accidentally surface unrelated authoring work. ``None`` means "no
        drafts" — the preview shows only the published baseline.

        ``extra_items`` are duck-typed content items appended after the
        include-filter has run; they always win over router items with the
        same id. Used for tests and direct callers with pre-built payloads.

        ``theme_css`` overrides the active theme stylesheet for this build only.
        The result is written to ``output_dir`` without touching output_root.
        """
        import json
        import logging
        import os
        import subprocess
        import tempfile

        from cauldron_content.homepage import (
            HOMEPAGE_COLLECTION,
            HOMEPAGE_ITEM_ID,
            HOMEPAGE_ROUTE,
        )

        logger = logging.getLogger(__name__)
        cfg = self._config

        if not cfg.frontend_root:
            return BuildResult(
                ok=False,
                error=(
                    "cauldron.site.astro frontend_root must be configured."
                ),
            )

        frontend_root = Path(cfg.frontend_root)
        output_path = Path(output_dir)

        def _page_entry(item):
            if item.id == HOMEPAGE_ITEM_ID:
                route = HOMEPAGE_ROUTE
            else:
                route = f"/{item.slug}/"
            return {
                "id": item.id,
                "route": route,
                "title": item.data.get("title", ""),
                "navigation_title": item.data.get("navigation_title", ""),
                "summary": item.data.get("summary", ""),
                "body": item.body or "",
                "template": item.data.get("template", "page"),
                "seo_title": item.data.get("seo_title", ""),
                "meta_description": item.data.get("meta_description", ""),
                "canonical_url": item.data.get("canonical_url", ""),
                "robots_index": item.data.get("robots_index", True),
                "robots_follow": item.data.get("robots_follow", True),
                "social_title": item.data.get("social_title", ""),
                "social_description": item.data.get("social_description", ""),
                "social_image": item.data.get("social_image", ""),
            }

        pages_by_id: dict = {}

        # Baseline: PUBLISHED items only. Drafts are opted-in below via
        # item_ids_to_include, preventing the "all drafts" leak.
        try:
            published_items = self._router.list_items(
                HOMEPAGE_COLLECTION, include_drafts=False,
            )
        except Exception as exc:
            return BuildResult(ok=False, error=f"Failed to list pages: {exc}")

        for item in published_items:
            if getattr(item, "status", "published") != "published":
                # Router may return non-published sentinel; skip anyway.
                continue
            pages_by_id[item.id] = _page_entry(item)

        # Opt-in drafts: overlay just the requested draft item_ids.
        include_ids = set(item_ids_to_include or ())
        if include_ids:
            try:
                all_items = self._router.list_items(
                    HOMEPAGE_COLLECTION, include_drafts=True,
                )
            except Exception as exc:
                return BuildResult(ok=False, error=f"Failed to list pages: {exc}")

            for item in all_items:
                if item.id in include_ids:
                    pages_by_id[item.id] = _page_entry(item)

        # extra_items always win last (test hook / direct payload injection)
        for item in (extra_items or []):
            pages_by_id[item.id] = _page_entry(item)

        pages = list(pages_by_id.values())

        if not pages:
            # Write an empty output directory for the preview
            output_path.mkdir(parents=True, exist_ok=True)
            return BuildResult(ok=True, pages_built=0, output_dir=str(output_path))

        # Use provided theme_css, otherwise fall back to active theme
        effective_theme_css = theme_css
        if not effective_theme_css and cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_svc = SiteThemeService(cfg.theme_root)
                effective_theme_css = theme_svc.get_active_css()
            except Exception:
                pass

        manifest = {
            "api_version": MANIFEST_API_VERSION,
            "pages": pages,
            "theme": {"css_content": effective_theme_css},
        }
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="cauldron_astro_preview_")
            manifest_path = os.path.join(tmp_dir, "manifest.json")

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f)

            env = {
                **os.environ,
                "CAULDRON_MANIFEST": manifest_path,
                "CAULDRON_OUTDIR": str(output_path),
                "CAULDRON_IS_PREVIEW": "1",
            }
            proc = subprocess.run(
                [cfg.npm_command, "run", "build"],
                cwd=str(frontend_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=cfg.build_timeout,
            )
            build_log = (proc.stdout or "") + (proc.stderr or "")

            if proc.returncode != 0:
                logger.error(
                    "Astro preview build failed (exit %d):\n%s",
                    proc.returncode,
                    build_log[-2000:],
                )
                return BuildResult(
                    ok=False,
                    error=f"Astro preview build exited {proc.returncode}.",
                    build_log=build_log,
                )

            return BuildResult(
                ok=True,
                pages_built=len(pages),
                output_dir=str(output_path),
                build_log=build_log,
            )

        except subprocess.TimeoutExpired:
            return BuildResult(
                ok=False,
                error=f"Astro preview build timed out after {cfg.build_timeout}s.",
            )
        except Exception as exc:
            return BuildResult(ok=False, error=str(exc))
        finally:
            if tmp_dir and Path(tmp_dir).exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)


def get_build_service() -> SiteBuildService:
    """Construct a SiteBuildService from current Django settings."""
    from django.conf import settings

    from cauldron_content.registry import registry
    from cauldron_content.router import (
        ContentRouter,
        RouterConfig,
        build_registered_collections,
    )

    from cauldron_site_astro.config import get_site_astro_config

    cfg = get_site_astro_config()

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    routing_cfg = (modules.get("cauldron.content") or {}).get("routing") or {}
    default_provider = routing_cfg.get("default_provider", "") or ""
    router_config = RouterConfig(
        default_provider=default_provider,
        collections=routing_cfg.get("collections", {}),
        registered_collections=build_registered_collections(routing_cfg, default_provider),
    )
    router = ContentRouter(registry, router_config)
    return SiteBuildService(cfg, router)
