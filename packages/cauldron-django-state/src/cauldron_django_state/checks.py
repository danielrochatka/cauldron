"""Django system checks for cauldron.django.state."""
from __future__ import annotations

from django.core import checks


def _get_state_config():
    """Return the DjangoStateConfig for the current settings, or None."""
    try:
        from django.conf import settings
        from cauldron_django_state.config import DjangoStateConfig

        modules_setting = getattr(settings, "CAULDRON_MODULES", None)
        if modules_setting is None:
            return None
        state_config = modules_setting.get("cauldron.django.state")
        if state_config is None:
            return None
        if not isinstance(state_config, dict):
            state_config = {}
        return DjangoStateConfig.from_module_config(state_config)
    except Exception:
        return None


def _is_state_active() -> bool:
    """Return True if cauldron.django.state is listed in CAULDRON_MODULES."""
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        if modules is None:
            return False
        return "cauldron.django.state" in modules
    except Exception:
        return False


@checks.register(checks.Tags.database)
def check_state_config(app_configs, **kwargs):
    """Validate the cauldron.django.state configuration."""
    if not _is_state_active():
        return []

    from django.conf import settings

    errors = []
    modules_setting = getattr(settings, "CAULDRON_MODULES", {})
    state_cfg = modules_setting.get("cauldron.django.state", {})
    if not isinstance(state_cfg, dict):
        state_cfg = {}

    database_alias = state_cfg.get("database_alias", "default")

    if not isinstance(database_alias, str) or not database_alias:
        errors.append(
            checks.Error(
                "CAULDRON_MODULES['cauldron.django.state']['database_alias'] must be a "
                "non-empty string.",
                hint="Set database_alias to a valid string key from your DATABASES setting.",
                id="cauldron.state.E100",
            )
        )
        return errors

    databases = getattr(settings, "DATABASES", {})
    if database_alias not in databases:
        errors.append(
            checks.Error(
                f"cauldron.django.state is configured to use database alias "
                f"{database_alias!r}, but it is not present in DATABASES.",
                hint=f"Add '{database_alias}' to your DATABASES setting.",
                id="cauldron.state.E101",
            )
        )
        return errors

    errors.append(
        checks.Info(
            "cauldron.django.state: database configuration looks healthy.",
            id="cauldron.state.I001",
        )
    )
    return errors
