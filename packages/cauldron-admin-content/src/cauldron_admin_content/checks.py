"""Django system checks for cauldron.admin.content."""
from __future__ import annotations

from pathlib import Path

from django.core import checks


def _is_admin_content_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return modules is not None and "cauldron.admin.content" in modules
    except Exception:
        return False


def _resolve_routing(settings) -> dict:
    """Return the resolved routing dict (routing override or module block)."""
    override = getattr(settings, "CAULDRON_CONTENT_ROUTING", None)
    if isinstance(override, dict):
        return override
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    content_cfg = modules.get("cauldron.content") or {}
    routing = content_cfg.get("routing") or {}
    return routing if isinstance(routing, dict) else {}


def _flatfile_is_routed(settings) -> bool:
    routing = _resolve_routing(settings)
    default = routing.get("default_provider", "") or ""
    collections = routing.get("collections", {}) or {}
    providers: set[str] = {default} if default else set()
    if isinstance(collections, dict):
        providers.update(v for v in collections.values() if isinstance(v, str))
    return "flatfile" in providers


@checks.register(checks.Tags.compatibility)
def check_admin_content_dependencies(app_configs, **kwargs):
    if not _is_admin_content_active():
        return []
    errors = []
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    required = [
        "django.contrib.admin",
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "cauldron_content_operations",
        "cauldron_admin_content",
    ]
    for app in required:
        if app not in installed:
            errors.append(checks.Error(
                f"cauldron.admin.content requires {app!r} in INSTALLED_APPS.",
                id="cauldron.admin.content.E900",
            ))
    if not errors:
        errors.append(checks.Info(
            "cauldron.admin.content configuration looks healthy.",
            id="cauldron.admin.content.I001",
        ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_admin_content_configuration(app_configs, **kwargs):
    """Item 14: verify the runtime configuration required to build a
    ContentOperationService.

    Emits stable error IDs so operators can filter/monitor:
      * content_admin.E001 — missing workspace root
      * content_admin.E002 — missing content root
      * content_admin.E003 — workspace init failure
      * content_admin.E004 — lock-directory failure
      * content_admin.E005 — adapter registration failure
      * content_admin.E006 — adapter/configuration mismatch
    """
    if not _is_admin_content_active():
        return []
    from django.conf import settings
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    errors = []
    ws_cfg = modules.get("cauldron.workspace.flatfile") or {}
    wp = ws_cfg.get("workspace_root", "")
    if not wp:
        errors.append(checks.Error(
            "cauldron.workspace.flatfile.workspace_root is required.",
            id="content_admin.E001",
        ))
    # Item 15: content_root and adapter checks only fire when the flatfile
    # CMS module is configured. Non-flatfile providers don't need either.
    cms_cfg = modules.get("cauldron.cms.flatfile")
    if cms_cfg is None:
        return errors
    content_root = (cms_cfg or {}).get("content_root", "")
    if not content_root:
        errors.append(checks.Error(
            "cauldron.cms.flatfile.content_root is required for cauldron.admin.content.",
            id="content_admin.E002",
        ))
    if errors:
        return errors

    workspace_config = None
    try:
        from cauldron_workspace_flatfile.config import WorkspaceConfig
        workspace_config = WorkspaceConfig(workspace_root=wp)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(checks.Error(
            f"Failed to construct workspace config: {type(exc).__name__}",
            id="content_admin.E003",
        ))
        return errors
    try:
        locks_dir = Path(workspace_config.locks_dir)
        locks_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        errors.append(checks.Error(
            "Workspace locks directory could not be prepared.",
            id="content_admin.E004",
        ))
    try:
        from cauldron_workspace_flatfile.reversible import (
            FlatFileReversibleMutationAdapter,
        )
        adapter = FlatFileReversibleMutationAdapter(workspace_config, content_root)
    except Exception as exc:
        errors.append(checks.Error(
            f"Failed to construct flatfile reversible adapter: {type(exc).__name__}",
            id="content_admin.E005",
        ))
        return errors
    try:
        if str(adapter._content_root) != str(Path(content_root).resolve()):
            errors.append(checks.Error(
                "Adapter/configuration mismatch: content_root does not match.",
                id="content_admin.E006",
            ))
    except Exception:
        errors.append(checks.Error(
            "Adapter/configuration mismatch: cannot verify content_root.",
            id="content_admin.E006",
        ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_admin_content_flatfile_routing(app_configs, **kwargs):
    """Routing-aware flatfile checks (Item 4 of the frozen contract pass).

    Emits:
      * content_admin.E020 — flatfile routed but flatfile module missing
      * content_admin.E021 — flatfile routed but content_root missing/empty
      * content_admin.E022 — flatfile routed but repository not registered
      * content_admin.E023 — flatfile routed but adapter contract fails
    """
    if not _is_admin_content_active():
        return []
    from django.conf import settings
    if not _flatfile_is_routed(settings):
        return []

    errors = []
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cms_cfg = modules.get("cauldron.cms.flatfile")
    if cms_cfg is None:
        errors.append(checks.Error(
            "Routing selects 'flatfile' but CAULDRON_MODULES["
            "'cauldron.cms.flatfile'] is not configured.",
            id="content_admin.E020",
        ))
        return errors
    content_root = (cms_cfg or {}).get("content_root", "")
    if not content_root:
        errors.append(checks.Error(
            "Routing selects 'flatfile' but 'content_root' is missing.",
            id="content_admin.E021",
        ))
        return errors

    # Item 4: the flatfile CMS app must be installed so the repository can
    # be constructed by callers. Registry population happens lazily at
    # runtime (get_service), so we check installability, not registration.
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    if "cauldron_cms_flatfile" not in installed:
        errors.append(checks.Error(
            "Routing selects 'flatfile' but 'cauldron_cms_flatfile' is not "
            "in INSTALLED_APPS.",
            id="content_admin.E022",
        ))

    # Item 4: adapter contract must validate.
    try:
        from cauldron_workspace_flatfile.config import WorkspaceConfig
        from cauldron_workspace_flatfile.reversible import (
            FlatFileReversibleMutationAdapter,
        )
        from cauldron_content_operations.reversible import (
            validate_adapter_contract,
        )
        ws_cfg = modules.get("cauldron.workspace.flatfile") or {}
        wp = ws_cfg.get("workspace_root", "")
        if wp:
            adapter = FlatFileReversibleMutationAdapter(
                WorkspaceConfig(workspace_root=wp), content_root,
            )
            violations = validate_adapter_contract(adapter)
            if violations:
                errors.append(checks.Error(
                    "Flatfile adapter does not satisfy the v2 rollback "
                    f"contract: {'; '.join(violations)[:200]}",
                    id="content_admin.E023",
                ))
    except Exception as exc:
        errors.append(checks.Error(
            f"Failed to validate flatfile adapter contract: "
            f"{type(exc).__name__}",
            id="content_admin.E023",
        ))

    return errors
