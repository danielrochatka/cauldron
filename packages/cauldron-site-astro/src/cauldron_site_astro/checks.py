"""Django system checks for cauldron.site.astro."""
from __future__ import annotations

import shutil
from pathlib import Path

from django.core import checks


def _is_active() -> bool:
    try:
        from django.conf import settings

        modules = getattr(settings, "CAULDRON_MODULES", None)
        return modules is not None and "cauldron.site.astro" in modules
    except Exception:
        return False


def _get_cfg() -> dict:
    from django.conf import settings

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    return modules.get("cauldron.site.astro") or {}


@checks.register(checks.Tags.compatibility)
def check_site_astro_config(app_configs, **kwargs):
    if not _is_active():
        return []

    errors = []
    cfg = _get_cfg()

    frontend_root = cfg.get("frontend_root", "")
    output_root = cfg.get("output_root", "")
    npm_command = cfg.get("npm_command", "npm")

    if not frontend_root:
        errors.append(
            checks.Error(
                "cauldron.site.astro.frontend_root is required.",
                id="cauldron.site.astro.E100",
            )
        )
    else:
        fr = Path(frontend_root)
        if not fr.is_dir():
            errors.append(
                checks.Error(
                    f"cauldron.site.astro.frontend_root {str(fr)!r} does not exist.",
                    id="cauldron.site.astro.E101",
                )
            )
        elif not (fr / "package.json").is_file():
            errors.append(
                checks.Error(
                    f"No package.json found in frontend_root {str(fr)!r}.",
                    id="cauldron.site.astro.E102",
                )
            )
        if output_root and frontend_root:
            try:
                or_path = Path(output_root).resolve()
                fr_path = fr.resolve()
                or_path.relative_to(fr_path)
                errors.append(
                    checks.Error(
                        "cauldron.site.astro.output_root must be outside frontend_root.",
                        id="cauldron.site.astro.E103",
                    )
                )
            except ValueError:
                pass

    if not shutil.which(npm_command):
        errors.append(
            checks.Warning(
                f"npm command {npm_command!r} not found. Site builds will fail.",
                id="cauldron.site.astro.W110",
            )
        )

    if not errors:
        errors.append(
            checks.Info(
                "cauldron.site.astro: configuration looks healthy.",
                id="cauldron.site.astro.I120",
            )
        )

    return errors
