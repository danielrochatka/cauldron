"""Entry-point discovery for installed Cauldron modules."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from . import CauldronModule

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cauldron.modules"


def discover_modules(*, entry_point_group: str = ENTRY_POINT_GROUP) -> list[CauldronModule]:
    """Return all modules registered under the cauldron.modules entry-point group."""
    eps = entry_points(group=entry_point_group)
    modules: list[CauldronModule] = []
    for ep in eps:
        try:
            obj = ep.load()
            if callable(obj) and not isinstance(obj, CauldronModule):
                obj = obj()
            if not isinstance(obj, CauldronModule):
                logger.warning("Entry point %r did not yield a CauldronModule; skipping.", ep.name)
                continue
            modules.append(obj)
            logger.debug("Discovered module %r from entry point %r.", obj.slug, ep.name)
        except Exception:
            logger.exception("Failed to load module from entry point %r.", ep.name)
    return modules
