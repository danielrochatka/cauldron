"""Routes content operations to the correct provider."""
from __future__ import annotations

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

    def _get_repo(self, provider_name: str) -> ContentRepository:
        repo = self._registry.get(provider_name)
        if repo is None:
            raise RouterError(f"Provider {provider_name!r} is not registered.")
        return repo

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
        return self._get_repo(provider).get_by_id(item_id, include_drafts=include_drafts)

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
        collection = changeset.operations[0].collection
        provider = self._resolve_provider(collection)
        return self._get_repo(provider).apply(changeset)
