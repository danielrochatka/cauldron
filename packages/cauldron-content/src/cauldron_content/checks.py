"""Django system checks for cauldron.content."""
from __future__ import annotations

from django.core import checks


def _is_content_active() -> bool:
    try:
        from django.conf import settings

        modules = getattr(settings, "CAULDRON_MODULES", None)
        if modules is None:
            return False
        return "cauldron.content" in modules
    except Exception:
        return False


def _get_content_config() -> dict:
    from django.conf import settings

    modules_setting = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules_setting.get("cauldron.content") or {}
    if not isinstance(cfg, dict):
        return {}
    return cfg


@checks.register(checks.Tags.compatibility)
def check_content_routing(app_configs, **kwargs):
    """Validate the CAULDRON_MODULES['cauldron.content'] routing config."""
    if not _is_content_active():
        return []

    errors: list = []
    cfg = _get_content_config()

    routing = cfg.get("routing")
    if routing is None:
        errors.append(
            checks.Info(
                "cauldron.content: no routing configuration set (using defaults).",
                id="cauldron.content.I400",
            )
        )
        return errors

    if not isinstance(routing, dict):
        errors.append(
            checks.Error(
                "CAULDRON_MODULES['cauldron.content']['routing'] must be a dict.",
                hint="Provide {'default_provider': str, 'collections': {name: provider}}.",
                id="cauldron.content.E400",
            )
        )
        return errors

    default_provider = routing.get("default_provider", "")
    collections = routing.get("collections", {}) or {}

    if not isinstance(collections, dict):
        errors.append(
            checks.Error(
                "routing.collections must be a mapping of collection -> provider name.",
                id="cauldron.content.E400",
            )
        )
        return errors

    # We can only validate provider names once the runtime registry is populated;
    # at check time we validate types only, and defer availability to health checks.
    if default_provider and not isinstance(default_provider, str):
        errors.append(
            checks.Error(
                "routing.default_provider must be a string.",
                id="cauldron.content.E401",
            )
        )

    for coll_name, provider_name in collections.items():
        if not isinstance(coll_name, str) or not coll_name:
            errors.append(
                checks.Error(
                    f"routing.collections has invalid collection key {coll_name!r}.",
                    id="cauldron.content.E402",
                )
            )
        if not isinstance(provider_name, str) or not provider_name:
            errors.append(
                checks.Error(
                    f"routing.collections[{coll_name!r}] must be a non-empty string.",
                    id="cauldron.content.E402",
                )
            )

    if not errors:
        errors.append(
            checks.Info(
                "cauldron.content: routing configuration looks healthy.",
                id="cauldron.content.I400",
            )
        )
    return errors
