"""Tests for ContentRouter routing behaviour."""
import pytest

from cauldron_content.contracts import (
    ApplyResult,
    ContentChangeSet,
    ContentItem,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
)
from cauldron_content.registry import RepositoryRegistry
from cauldron_content.router import ContentRouter, RouterConfig, RouterError


class _RecordingRepo:
    def __init__(self, name: str, items: list[ContentItem] | None = None):
        self.name = name
        self._items = items or []
        self.calls: list[tuple] = []

    def describe(self): ...
    def list_collections(self): return []
    def list_items(self, collection, *, include_drafts=False):
        self.calls.append(("list_items", collection, include_drafts))
        return [i for i in self._items if i.collection == collection]

    def get_by_id(self, item_id, *, include_drafts=False):
        self.calls.append(("get_by_id", item_id, include_drafts))
        return next((i for i in self._items if i.id == item_id), None)

    def get_by_slug(self, collection, slug, *, include_drafts=False):
        self.calls.append(("get_by_slug", collection, slug, include_drafts))
        return next(
            (i for i in self._items if i.collection == collection and i.slug == slug),
            None,
        )

    def validate(self, item): ...
    def apply(self, changeset):
        self.calls.append(("apply", changeset.id))
        return ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())

    def health(self): ...


def _item(collection: str = "c", slug: str = "s", id_: str = "id.1") -> ContentItem:
    return ContentItem(
        id=id_,
        collection=collection,
        slug=slug,
        status=ContentStatus.PUBLISHED,
        schema="sc",
        data={},
        body="",
        hash="",
        provider="p",
    )


def test_default_routing_used_when_collection_unknown():
    reg = RepositoryRegistry()
    repo = _RecordingRepo("p")
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(default_provider="p"))
    router.list_items("pages")
    assert repo.calls == [("list_items", "pages", False)]


def test_per_collection_routing_overrides_default():
    reg = RepositoryRegistry()
    r1 = _RecordingRepo("p1")
    r2 = _RecordingRepo("p2")
    reg.register("p1", r1)
    reg.register("p2", r2)
    router = ContentRouter(
        reg, RouterConfig(default_provider="p1", collections={"posts": "p2"})
    )
    router.list_items("posts")
    router.list_items("pages")
    assert r2.calls == [("list_items", "posts", False)]
    assert r1.calls == [("list_items", "pages", False)]


def test_unknown_provider_raises_router_error():
    reg = RepositoryRegistry()
    router = ContentRouter(reg, RouterConfig(default_provider="missing"))
    with pytest.raises(RouterError):
        router.list_items("pages")


def test_no_provider_configured_raises():
    reg = RepositoryRegistry()
    router = ContentRouter(reg, RouterConfig())
    with pytest.raises(RouterError):
        router.list_items("pages")


def test_get_by_id_without_collection_uses_default():
    reg = RepositoryRegistry()
    repo = _RecordingRepo("p")
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(default_provider="p"))
    router.get_by_id("id.1")
    assert repo.calls[0][0] == "get_by_id"


def test_get_by_id_without_collection_or_default_raises():
    reg = RepositoryRegistry()
    router = ContentRouter(reg, RouterConfig())
    with pytest.raises(RouterError):
        router.get_by_id("id.1")


def test_apply_routes_by_first_operation_collection():
    reg = RepositoryRegistry()
    repo = _RecordingRepo("p")
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(collections={"posts": "p"}))
    cs = ContentChangeSet(
        id="cs.1",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="p",
                collection="posts",
                item_id="id.1",
            ),
        ),
    )
    result = router.apply(cs)
    assert result.success is True
    assert repo.calls == [("apply", "cs.1")]


def test_apply_empty_changeset_returns_success():
    reg = RepositoryRegistry()
    router = ContentRouter(reg, RouterConfig())
    result = router.apply(ContentChangeSet(id="cs.empty", operations=()))
    assert result.success is True


def test_get_by_slug_routes_by_collection():
    reg = RepositoryRegistry()
    repo = _RecordingRepo("p")
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(collections={"pages": "p"}))
    router.get_by_slug("pages", "home")
    assert repo.calls[0][:3] == ("get_by_slug", "pages", "home")
