from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

HOMEPAGE_ITEM_ID = "homepage"


@dataclass(frozen=True)
class SiteAstroConfig:
    frontend_root: str
    output_root: str
    homepage_item_id: str = HOMEPAGE_ITEM_ID
    npm_command: str = "npm"
    build_timeout: int = 120


def get_site_astro_config() -> SiteAstroConfig:
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.site.astro") or {}
    return SiteAstroConfig(
        frontend_root=cfg.get("frontend_root", ""),
        output_root=cfg.get("output_root", ""),
        homepage_item_id=cfg.get("homepage_item_id", HOMEPAGE_ITEM_ID),
        npm_command=cfg.get("npm_command", "npm"),
        build_timeout=int(cfg.get("build_timeout", 120)),
    )
