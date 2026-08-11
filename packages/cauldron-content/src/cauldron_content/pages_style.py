"""Registration seam for public-site pages-style composition.

This module holds a single optional provider slot.  Callers must not import
from the concrete provider package (cauldron-django-admin) — they use the
accessors here instead.

``cauldron-django-admin`` registers a ``PagesStyleProvider`` during its
``AppConfig.ready()``.  ``cauldron-site-astro`` reads the provider during
``SiteChangeSetService.publish()`` so that style commits happen atomically
before any live output/theme promotion.

If no provider is registered (e.g. the override store module is absent), all
accessors return safe no-op results so site-astro degrades gracefully.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class StyleConflictError(Exception):
    """Raised by PagesStyleProvider when a hash conflict is detected.

    Concrete implementations translate backend-specific conflict errors (e.g.
    ``HashConflictError`` from UIOverrideStore) to this type so callers do not
    need to import from ``cauldron-django-admin``.
    """


@runtime_checkable
class PagesStyleProvider(Protocol):
    """Provides composed public-site pages CSS to the publication workflow."""

    def get_composed_css(
        self,
        *,
        proposed_target: str | None = None,
        proposed_content: str | None = None,
    ) -> str:
        """Return the effective public-site CSS.

        When *proposed_target* / *proposed_content* are given, overlay that
        one file in the composition (lexical sort order preserved).  Otherwise
        return only the currently-applied files concatenated.

        Returns empty string when there are no pages override files and no
        proposed content.
        """
        ...

    def read_style_source(self, target: str) -> str | None:
        """Read the current content of a pages CSS source file.

        Returns ``None`` when the file does not exist.  Used to capture a
        pre-image for rollback before committing a style change.
        """
        ...

    def commit_style(
        self,
        *,
        target: str,
        content: str,
        expected_hash: str,
        base_exists: bool,
    ) -> str:
        """Write a proposed CSS file to the store atomically.

        Uses optimistic locking via *expected_hash* so concurrent modifications
        fail rather than silently overwriting.  Returns the new hash of the
        written content.

        Raises :exc:`StyleConflictError` if *expected_hash* does not match the
        current on-disk state.  Concrete implementations must translate any
        backend-specific conflict errors to this type.
        """
        ...

    def rollback_style_commit(
        self,
        *,
        target: str,
        old_content: str | None,
        committed_hash: str,
    ) -> bool:
        """Attempt to undo a previous :meth:`commit_style` call.

        *old_content* is the content that existed before the commit (``None``
        means the file was newly created).  *committed_hash* is the hash
        returned by the prior :meth:`commit_style` call and is used as the
        optimistic lock value for the rollback write.

        Returns ``True`` if the rollback succeeded, ``False`` if it failed
        (callers should set reconciliation evidence in the latter case).
        """
        ...

    def list_targets(self) -> list[str]:
        """Return sorted list of current pages CSS relative paths."""
        ...


_provider: PagesStyleProvider | None = None


def register_pages_style_provider(provider: PagesStyleProvider | None) -> None:
    """Register (or clear) the public-site pages-style provider.

    Only one provider may be registered at a time.  Calling with ``None``
    clears the registration.  Re-registering the same instance is idempotent.
    """
    global _provider
    _provider = provider


def get_pages_style_provider() -> PagesStyleProvider | None:
    """Return the registered provider, or ``None`` if none is registered."""
    return _provider
