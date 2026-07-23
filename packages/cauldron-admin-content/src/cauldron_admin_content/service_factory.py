"""Build a ContentOperationService from Django settings."""
from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger(__name__)


def _routing_config(settings) -> dict:
    """Return the resolved routing config from CAULDRON_MODULES."""
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    content_cfg = modules.get("cauldron.content") or {}
    return content_cfg.get("routing") or {}


def _flatfile_is_routed(settings) -> bool:
    """Return True iff the ``flatfile`` provider is referenced by routing.

    Reads either ``CAULDRON_CONTENT_ROUTING`` (setting override) or the
    routing block inside ``CAULDRON_MODULES['cauldron.content']``. Presence
    in the default provider or any per-collection route counts.
    """
    routing = getattr(settings, "CAULDRON_CONTENT_ROUTING", None)
    if routing is None:
        routing = _routing_config(settings)
    if not isinstance(routing, dict):
        return False
    default = routing.get("default_provider", "") or ""
    collections = routing.get("collections", {}) or {}
    providers: set[str] = {default} if default else set()
    if isinstance(collections, dict):
        providers.update(v for v in collections.values() if isinstance(v, str))
    return "flatfile" in providers


def get_service():
    """Return a configured ContentOperationService from current Django settings.

    Item 4 (this pass): the flatfile CMS module is only mandatory when the
    routing config actually selects the flatfile provider. Non-flatfile
    routing skips the content_root/adapter checks entirely.
    """
    from django.conf import settings
    from cauldron_content.registry import registry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import get_operations_config

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    routing_cfg = _routing_config(settings)

    router_config = RouterConfig(
        default_provider=routing_cfg.get("default_provider", ""),
        collections=routing_cfg.get("collections", {}),
    )
    router = ContentRouter(registry, router_config)

    ws_cfg_dict = modules.get("cauldron.workspace.flatfile") or {}
    wp = ws_cfg_dict.get("workspace_root", "")
    if not wp:
        raise ImproperlyConfigured(
            "Workspace root is required for cauldron.admin.content; "
            "configure CAULDRON_MODULES['cauldron.workspace.flatfile']['workspace_root']."
        )
    try:
        from cauldron_workspace_flatfile.config import WorkspaceConfig
        from cauldron_workspace_flatfile.store import ChangeSetStore
        from cauldron_workspace_flatfile.snapshots import SnapshotService
        workspace_config = WorkspaceConfig(workspace_root=wp)
        workspace = ChangeSetStore(workspace_config)
        snapshots = SnapshotService(workspace_config)
        locks_dir = workspace_config.locks_dir
    except Exception as exc:
        raise ImproperlyConfigured(
            f"Failed to initialize workspace for cauldron.admin.content: {type(exc).__name__}"
        ) from exc

    required_reversible: frozenset[str] = frozenset()
    flatfile_routed = _flatfile_is_routed(settings)

    if flatfile_routed:
        # Item 4: routing selects flatfile → module config, repository, and
        # v2 adapter are all mandatory.
        cms_cfg = modules.get("cauldron.cms.flatfile")
        if cms_cfg is None:
            raise ImproperlyConfigured(
                "Routing selects 'flatfile' but CAULDRON_MODULES["
                "'cauldron.cms.flatfile'] is not configured."
            )
        content_root = (cms_cfg or {}).get("content_root", "")
        if not content_root:
            raise ImproperlyConfigured(
                "content_root is required for cauldron.admin.content when "
                "routing selects 'flatfile'."
            )
        # Repository availability: prefer runtime registration, but accept
        # the cauldron_cms_flatfile app being installed as sufficient
        # evidence that the repository can be constructed on demand.
        installed = list(getattr(settings, "INSTALLED_APPS", []))
        try:
            repo = registry.get("flatfile")
        except Exception:
            repo = None
        if repo is None and "cauldron_cms_flatfile" not in installed:
            raise ImproperlyConfigured(
                "flatfile content repository is not registered and "
                "'cauldron_cms_flatfile' is not in INSTALLED_APPS."
            )
        try:
            from cauldron_workspace_flatfile.reversible import (
                FlatFileReversibleMutationAdapter,
            )
            from cauldron_content_operations.reversible import (
                register_adapter, get_adapter, unregister_adapter,
            )
            adapter = FlatFileReversibleMutationAdapter(workspace_config, content_root)
            # Verify compatibility: content_root and workspace paths match
            # the adapter we just constructed. Guards against config drift.
            if str(adapter._content_root) != str(
                __import__("pathlib").Path(content_root).resolve()
            ):
                raise ImproperlyConfigured(
                    "Constructed flatfile adapter has a content_root mismatch."
                )
            existing = get_adapter("flatfile")
            if existing is not None and existing is not adapter:
                # Never retain a stale adapter across configuration changes.
                existing_root = getattr(existing, "_content_root", None)
                if existing_root is not None and str(existing_root) != str(adapter._content_root):
                    unregister_adapter("flatfile")
            register_adapter("flatfile", adapter)
        except ImproperlyConfigured:
            raise
        except Exception as exc:
            raise ImproperlyConfigured(
                "Failed to register flatfile reversible adapter for "
                f"cauldron.admin.content: {type(exc).__name__}"
            ) from exc
        required_reversible = frozenset({"flatfile"})
    else:
        # Item 4: non-flatfile routing must NOT require flatfile module
        # config or register the flatfile adapter.
        cms_cfg = modules.get("cauldron.cms.flatfile")
        if cms_cfg is not None:
            # Backward compatibility: if the flatfile module is declared,
            # register its adapter opportunistically so that pre-routing
            # setups continue to work. Absent module config means no adapter
            # is required or registered.
            content_root = (cms_cfg or {}).get("content_root", "")
            if not content_root:
                raise ImproperlyConfigured(
                    "content_root is required for cauldron.admin.content when "
                    "cauldron.cms.flatfile is configured; configure "
                    "CAULDRON_MODULES['cauldron.cms.flatfile']['content_root']."
                )
            try:
                from cauldron_workspace_flatfile.reversible import (
                    FlatFileReversibleMutationAdapter,
                )
                from cauldron_content_operations.reversible import (
                    register_adapter, get_adapter, unregister_adapter,
                )
                adapter = FlatFileReversibleMutationAdapter(workspace_config, content_root)
                if str(adapter._content_root) != str(
                    __import__("pathlib").Path(content_root).resolve()
                ):
                    raise ImproperlyConfigured(
                        "Constructed flatfile adapter has a content_root mismatch."
                    )
                existing = get_adapter("flatfile")
                if existing is not None and existing is not adapter:
                    existing_root = getattr(existing, "_content_root", None)
                    if existing_root is not None and str(existing_root) != str(adapter._content_root):
                        unregister_adapter("flatfile")
                register_adapter("flatfile", adapter)
            except ImproperlyConfigured:
                raise
            except Exception as exc:
                raise ImproperlyConfigured(
                    "Failed to register flatfile reversible adapter for "
                    f"cauldron.admin.content: {type(exc).__name__}"
                ) from exc

    return ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=get_operations_config(),
        locks_dir=locks_dir,
        required_reversible_providers=required_reversible,
    )
