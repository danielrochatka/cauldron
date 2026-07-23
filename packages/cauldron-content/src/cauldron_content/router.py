"""Routes content operations to the correct provider."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Optional

from .contracts import ApplyResult, ContentChangeSet, ContentItem, ContentRepository
from .registry import RepositoryRegistry


@dataclass
class RouterConfig:
    default_provider: str = ""
    collections: dict[str, str] = field(default_factory=dict)


class RouterError(Exception):
    pass


class ContentRouter:
    def __init__(self, registry: RepositoryRegistry, config: RouterConfig) -> None:
        self._registry = registry
        self._config = RouterConfig(
            default_provider=config.default_provider,
            collections=dict(config.collections),
        )

    def _resolve_provider(self, collection: str) -> str:
        if collection in self._config.collections:
            return self._config.collections[collection]
        if self._config.default_provider:
            return self._config.default_provider
        raise RouterError(
            f"No provider configured for collection {collection!r} and no default provider set."
        )

    def resolve_provider(self, collection: str) -> str:
        """Public: resolve the provider name for a collection.

        Raises :class:`RouterError` when the collection is not routable.
        """
        return self._resolve_provider(collection)

    def list_collections(self) -> list[str]:
        """Return all collections visible across all registered providers."""
        collections: set[str] = set()
        for name in self._registry.names():
            repo = self._registry.get(name)
            if repo is None:
                continue
            try:
                collections.update(repo.list_collections())
            except Exception:
                # A single misbehaving provider must not break enumeration.
                continue
        return sorted(collections)

    def _get_repo(self, provider_name: str) -> ContentRepository:
        repo = self._registry.get(provider_name)
        if repo is None:
            raise RouterError(f"Provider {provider_name!r} is not registered.")
        return repo

    def get_repo(self, provider_name: str) -> ContentRepository:
        """Public access to the registered repository for a provider."""
        return self._get_repo(provider_name)

    def list_items(
        self, collection: str, *, include_drafts: bool = False
    ) -> list[ContentItem]:
        provider = self._resolve_provider(collection)
        return self._get_repo(provider).list_items(
            collection, include_drafts=include_drafts
        )

    def get_by_id(
        self,
        item_id: str,
        collection: str = "",
        *,
        include_drafts: bool = False,
    ) -> Optional[ContentItem]:
        if collection:
            provider = self._resolve_provider(collection)
        elif self._config.default_provider:
            provider = self._config.default_provider
        else:
            raise RouterError(
                "Cannot route get_by_id without collection or default provider."
            )
        repo = self._get_repo(provider)
        # Item 11: capability-detect a ``collection`` kwarg using the
        # ``CollectionAwareRepository`` protocol rather than the presence of
        # ``**kwargs``. A repo that only accepts ``**kwargs`` MUST fall back
        # to the list_items path so we never accidentally leak same-id items
        # from other collections.
        try:
            sig = inspect.signature(repo.get_by_id)
        except (TypeError, ValueError):
            sig = None
        supports_collection = False
        if sig is not None:
            for name, param in sig.parameters.items():
                if name == "collection" and param.kind in (
                    inspect.Parameter.KEYWORD_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    supports_collection = True
                    break
        if supports_collection:
            return repo.get_by_id(
                item_id, include_drafts=include_drafts, collection=collection,
            )
        # Fallback: enumerate only the requested collection so a same-id
        # item in a different collection cannot pollute the result.
        if collection:
            items = repo.list_items(collection, include_drafts=True)
            for it in items:
                if it.id == item_id:
                    if not include_drafts and getattr(it, "status", None) is not None:
                        # ContentStatus enum; DRAFT filter.
                        try:
                            from .contracts import ContentStatus
                            if it.status == ContentStatus.DRAFT and not include_drafts:
                                return None
                        except Exception:
                            pass
                    return it
            return None
        # No collection and no collection-aware repo: preserve legacy behaviour.
        return repo.get_by_id(item_id, include_drafts=include_drafts)

    def get_by_slug(
        self,
        collection: str,
        slug: str,
        *,
        include_drafts: bool = False,
    ) -> Optional[ContentItem]:
        provider = self._resolve_provider(collection)
        return self._get_repo(provider).get_by_slug(
            collection, slug, include_drafts=include_drafts
        )

    def apply(self, changeset: ContentChangeSet) -> ApplyResult:
        if not changeset.operations:
            return ApplyResult(
                success=True,
                applied=(),
                conflicts=(),
                validation_errors=(),
            )

        # Group operations by provider, preserving order.
        by_provider: dict[str, list] = {}
        for op in changeset.operations:
            provider = self._resolve_provider(op.collection)
            if provider not in by_provider:
                by_provider[provider] = []
            by_provider[provider].append(op)

        if len(by_provider) == 1:
            provider = next(iter(by_provider))
            return self._get_repo(provider).apply(changeset)

        # Mixed providers: apply one sub-changeset per provider and merge.
        all_applied: list = []
        all_conflicts: list = []
        all_errors: list = []
        for provider, ops in by_provider.items():
            sub = ContentChangeSet(
                id=changeset.id,
                operations=tuple(ops),
                author=changeset.author,
                description=changeset.description,
                metadata=changeset.metadata,
            )
            result = self._get_repo(provider).apply(sub)
            all_applied.extend(result.applied)
            all_conflicts.extend(result.conflicts)
            all_errors.extend(result.validation_errors)

        success = not all_conflicts and not all_errors
        return ApplyResult(
            success=success,
            applied=tuple(all_applied) if success else (),
            conflicts=tuple(all_conflicts),
            validation_errors=tuple(all_errors),
        )
