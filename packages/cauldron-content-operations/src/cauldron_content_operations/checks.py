"""Django system checks for cauldron.content.operations."""
from __future__ import annotations

from django.core import checks


def _is_operations_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return modules is not None and "cauldron.content.operations" in modules
    except Exception:
        return False


@checks.register(checks.Tags.compatibility)
def check_operations_dependencies(app_configs, **kwargs):
    if not _is_operations_active():
        return []
    errors = []
    required_apps = [
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "cauldron_content",
        "cauldron_workspace_flatfile",
        "cauldron_content_operations",
    ]
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    for app in required_apps:
        if app not in installed:
            errors.append(checks.Error(
                f"cauldron.content.operations requires {app!r} in INSTALLED_APPS.",
                id="cauldron.content.operations.E700",
            ))

    required_middleware = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    for mw in required_middleware:
        if mw not in middleware:
            errors.append(checks.Error(
                f"cauldron.content.operations requires {mw!r} in MIDDLEWARE.",
                id="cauldron.content.operations.E701",
            ))

    return errors


@checks.register(checks.Tags.compatibility)
def check_operations_config(app_configs, **kwargs):
    if not _is_operations_active():
        return []
    errors = []
    from django.conf import settings
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.content.operations") or {}
    if not isinstance(cfg, dict):
        errors.append(checks.Error(
            "CAULDRON_MODULES['cauldron.content.operations'] must be a dict.",
            id="cauldron.content.operations.E702",
        ))
        return errors

    max_ops = cfg.get("max_operations_per_change_set", 100)
    if not isinstance(max_ops, int) or max_ops < 1:
        errors.append(checks.Error(
            "max_operations_per_change_set must be a positive integer.",
            id="cauldron.content.operations.E703",
        ))

    require_approval = cfg.get("require_approval", True)
    if not isinstance(require_approval, bool):
        errors.append(checks.Error(
            "require_approval must be a boolean.",
            id="cauldron.content.operations.E704",
        ))

    allow_self_approval = cfg.get("allow_self_approval", False)
    if not isinstance(allow_self_approval, bool):
        errors.append(checks.Error(
            "allow_self_approval must be a boolean.",
            id="cauldron.content.operations.E705",
        ))

    if not errors:
        errors.append(checks.Info(
            "cauldron.content.operations configuration looks healthy.",
            id="cauldron.content.operations.I001",
        ))
    return errors


@checks.register(checks.Tags.database)
def check_unresolved_transitional_requests(app_configs, **kwargs):
    """Warn if there are requests stuck in transitional states. DATABASE TOUCH."""
    if not _is_operations_active():
        return []
    try:
        from .lifecycle import LifecycleState
        from .models import ContentChangeRequest
        count = ContentChangeRequest.objects.filter(
            lifecycle_state__in=[
                LifecycleState.APPLYING.value,
                LifecycleState.ROLLING_BACK.value,
            ]
        ).count()
        if count:
            return [checks.Warning(
                f"{count} change request(s) are stuck in transitional states (applying/rolling_back).",
                hint="Run `python manage.py cauldron_content_reconcile` to inspect.",
                id="cauldron.content.operations.W700",
            )]
        return []
    except Exception:
        return []


@checks.register(checks.Tags.database)
def check_reconciliation_required(app_configs, **kwargs):
    """Warn if there are requests requiring reconciliation. DATABASE TOUCH."""
    if not _is_operations_active():
        return []
    try:
        from .lifecycle import LifecycleState
        from .models import ContentChangeRequest
        count = ContentChangeRequest.objects.filter(
            lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value
        ).count()
        if count:
            return [checks.Warning(
                f"{count} change request(s) require reconciliation.",
                hint="Run `python manage.py cauldron_content_reconcile` to inspect.",
                id="cauldron.content.operations.W701",
            )]
        return []
    except Exception:
        return []
