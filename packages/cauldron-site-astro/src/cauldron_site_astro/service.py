"""Site build service for cauldron-site-astro."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BuildResult:
    ok: bool
    pages_built: int = 0
    output_dir: str = ""
    error: str = ""
    build_log: str = ""


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
        import shutil
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
            logger.info("No published pages; skipping Astro build.")
            return BuildResult(ok=True, pages_built=0, output_dir=str(output_root))

        manifest = {"pages": pages}
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

            # Atomic replace: rename tmp_out over output_root
            output_root.parent.mkdir(parents=True, exist_ok=True)
            swap = Path(str(output_root) + ".prev")
            if output_root.exists():
                if swap.exists():
                    shutil.rmtree(swap)
                output_root.rename(swap)
            shutil.copytree(tmp_out, str(output_root))
            if swap.exists():
                shutil.rmtree(swap)

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
