"""Build a ContentOperationService from Django settings."""
from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger(__name__)


def get_service():
    """Return a configured ContentOperationService from current Django settings.

    Item 14: raises :class:`ImproperlyConfigured` on missing content_root or
    workspace_root, adapter construction failures, or content_root/workspace
    mismatch. Never swallows adapter registration failures.

    A stale globally-registered adapter from a previous configuration is
    unregistered before we install a new one so config drift cannot leave
    a mismatched adapter in place.
    """
    from django.conf import settings
    from cauldron_content.registry import registry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import get_operations_config

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    content_cfg = modules.get("cauldron.content") or {}
    routing_cfg = content_cfg.get("routing") or {}

    router_config = RouterConfig(
        default_provider=routing_cfg.get("default_provider", ""),
        collections=routing_cfg.get("collections", {}),
    )
    router = ContentRouter(registry, router_config)

    ws_cfg_dict = modules.get("cauldron.workspace.flatfile") or {}
    wp = ws_cfg_dict.get("workspace_root", "")
    if not wp:
        raise ImproperlyConfigured(
            "Workspace root is required for cauldron.content.api; "
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
        # Bounded message; no path leakage.
        raise ImproperlyConfigured(
            f"Failed to initialize workspace for cauldron.content.api: {type(exc).__name__}"
        ) from exc

    # Item 14: flat-file adapter registration is required.
    cms_cfg = modules.get("cauldron.cms.flatfile") or {}
    content_root = cms_cfg.get("content_root", "")
    if not content_root:
        raise ImproperlyConfigured(
            "content_root is required for cauldron.content.api; "
            "configure CAULDRON_MODULES['cauldron.cms.flatfile']['content_root']."
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
            f"cauldron.content.api: {type(exc).__name__}"
        ) from exc

    return ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=get_operations_config(),
        locks_dir=locks_dir,
    )
