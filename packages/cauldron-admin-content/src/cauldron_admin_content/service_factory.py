"""Compatibility re-export.

The ContentOperationService factory now lives in
``cauldron_content_operations.service_factory``, which is the module that
owns the service. This shim preserves the old import path for Admin Content
callers and any external code that imports from here.

``_build_registered_collections`` is a thin local wrapper around the public
``cauldron_content.router.build_registered_collections`` helper so the
admin-content test suite can continue to import it via this module without
crossing a package boundary into another module's private surface.
"""
from cauldron_content_operations.service_factory import (  # noqa: F401
    get_service,
)


def _build_registered_collections(routing_cfg: dict, default_provider: str) -> dict:
    from cauldron_content.router import build_registered_collections
    return build_registered_collections(routing_cfg, default_provider)


__all__ = ["get_service", "_build_registered_collections"]
