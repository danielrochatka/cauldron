"""Collection-aware repository protocol (Item 11).

The core ``ContentRepository`` protocol in :mod:`cauldron_content.contracts`
supports a legacy ``get_by_id(item_id, *, include_drafts)`` signature. Some
repositories additionally accept a ``collection`` kwarg so router lookups can
be scoped to a single collection instead of enumerating all collections.

``CollectionAwareRepository`` is a lightweight runtime-checkable protocol used
by :class:`ContentRouter.get_by_id` to detect that capability explicitly,
without relying on ``inspect.signature`` heuristics or ``**kwargs`` shape.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .contracts import ContentItem


@runtime_checkable
class CollectionAwareRepository(Protocol):
    """Structural protocol for repositories that scope ``get_by_id`` to a
    named collection. Implementations MUST accept ``collection`` as an
    explicit keyword argument."""

    def get_by_id(
        self,
        item_id: str,
        *,
        include_drafts: bool = False,
        collection: str = "",
    ) -> Optional[ContentItem]: ...


__all__ = ["CollectionAwareRepository"]
