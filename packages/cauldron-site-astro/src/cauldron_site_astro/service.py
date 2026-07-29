"""Site build service for cauldron-site-astro."""
from __future__ import annotations

import fcntl
import glob
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path


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
      2. Copy src_dir → staging path
      3. Rename output_root → previous path (if output_root exists)
      4. Rename staging → output_root  (atomic on same filesystem)
      5. Remove previous path

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
            # Step 2: copy src → staging
            shutil.copytree(str(src_dir), str(staging))

            # Step 3: move existing output out of the way
            if output_root.exists():
                output_root.rename(previous)

            # Step 4: move staging into place (atomic on same filesystem)
            staging.rename(output_root)

            # Step 5: remove old output
            if previous.exists():
                shutil.rmtree(previous)

        except Exception:
            # Restore: if previous exists and output_root is gone, put it back
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

    def build(self) -> BuildResult:
        """Build the full public site.

        1. Collect published pages from router.
        2. Write JSON manifest to a temporary file.
        3. Run ``npm run build`` in the frontend_root with CAULDRON_MANIFEST
           and CAULDRON_OUTDIR env vars.
        4. On success: atomically replace output_root with the temp output directory.
        5. On failure: leave output_root untouched.
        6. Clean up temp files regardless.
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

        # Read active theme CSS if theme_root is configured
        theme_css = ""
        if cfg.theme_root:
            try:
                from cauldron_site_astro.theme import SiteThemeService
                theme_svc = SiteThemeService(cfg.theme_root)
                theme_css = theme_svc.get_active_css()
            except Exception:
                pass  # Non-fatal: build without theme

        manifest = {"pages": pages, "theme": {"css_content": theme_css}}
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


    def build_preview(
        self,
        *,
        output_dir: "str | Path",
        extra_items: "list | None" = None,
        theme_css: str = "",
    ) -> BuildResult:
        """Build a preview including draft pages and a proposed theme.

        ``extra_items`` are duck-typed content items (same shape as returned
        by the router) added alongside published pages (deduplicated by id).
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

        # Collect published pages from router
        pages_by_id: dict = {}
        try:
            items = self._router.list_items(HOMEPAGE_COLLECTION, include_drafts=True)
        except Exception as exc:
            return BuildResult(ok=False, error=f"Failed to list pages: {exc}")

        for item in items:
            if item.id == HOMEPAGE_ITEM_ID:
                route = HOMEPAGE_ROUTE
            else:
                route = f"/{item.slug}/"
            pages_by_id[item.id] = {
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

        # Merge extra_items (deduplicate by id, extra_items win)
        for item in (extra_items or []):
            if item.id == HOMEPAGE_ITEM_ID:
                route = HOMEPAGE_ROUTE
            else:
                route = f"/{item.slug}/"
            pages_by_id[item.id] = {
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

        manifest = {"pages": pages, "theme": {"css_content": effective_theme_css}}
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
