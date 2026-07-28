"""Django system checks for cauldron.site.astro."""
from __future__ import annotations

import os
import shutil
import subprocess
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

    fr: Path | None = None

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
            fr = None
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
                fr_path = Path(frontend_root).resolve()
                or_path.relative_to(fr_path)
                errors.append(
                    checks.Error(
                        "cauldron.site.astro.output_root must be outside frontend_root.",
                        id="cauldron.site.astro.E103",
                    )
                )
            except ValueError:
                pass

    # W110 — npm executable missing
    if not shutil.which(npm_command):
        errors.append(
            checks.Warning(
                f"npm command {npm_command!r} not found in PATH. "
                "Site builds will fail. Run: ./install",
                id="cauldron.site.astro.W110",
            )
        )

    # W111 — package-lock.json missing (cannot do a reproducible install)
    if fr is not None and not (fr / "package-lock.json").is_file():
        errors.append(
            checks.Warning(
                f"No package-lock.json found in frontend_root {str(fr)!r}. "
                "Run: ./install",
                id="cauldron.site.astro.W111",
            )
        )

    # W112 / W113 / I121 — local Astro binary
    astro_ok = False
    if fr is not None:
        astro_bin = fr / "node_modules" / ".bin" / "astro"
        if not astro_bin.exists():
            errors.append(
                checks.Warning(
                    f"Astro is not installed in {str(fr)!r}. "
                    "Run: ./install",
                    id="cauldron.site.astro.W112",
                )
            )
        elif not os.access(str(astro_bin), os.X_OK):
            errors.append(
                checks.Warning(
                    f"Astro binary at {str(astro_bin)!r} is not executable. "
                    "Run: ./install",
                    id="cauldron.site.astro.W113",
                )
            )
        else:
            try:
                proc = subprocess.run(
                    [str(astro_bin), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    astro_version = proc.stdout.strip() or "unknown"
                    errors.append(
                        checks.Info(
                            f"Astro {astro_version} is installed and ready.",
                            id="cauldron.site.astro.I121",
                        )
                    )
                    astro_ok = True
                else:
                    errors.append(
                        checks.Warning(
                            "Astro binary failed to run. Run: ./install",
                            id="cauldron.site.astro.W113",
                        )
                    )
            except subprocess.TimeoutExpired:
                errors.append(
                    checks.Warning(
                        "Astro binary timed out. Run: ./install",
                        id="cauldron.site.astro.W113",
                    )
                )
            except OSError:
                errors.append(
                    checks.Warning(
                        "Astro binary could not be executed. Run: ./install",
                        id="cauldron.site.astro.W113",
                    )
                )

    # I120 — fully healthy (no errors, no warnings, Astro confirmed working)
    config_issues = [
        e for e in errors
        if e.id and not e.id.endswith(".I121")
    ]
    if not config_issues and astro_ok:
        errors.append(
            checks.Info(
                "cauldron.site.astro: configuration looks healthy.",
                id="cauldron.site.astro.I120",
            )
        )

    return errors
