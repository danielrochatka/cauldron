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


def test_apply_routes_mixed_provider_changeset_per_provider():
    reg = RepositoryRegistry()
    r1 = _RecordingRepo("p1")
    r2 = _RecordingRepo("p2")
    reg.register("p1", r1)
    reg.register("p2", r2)
    router = ContentRouter(
        reg,
        RouterConfig(collections={"pages": "p1", "media": "p2"}),
    )
    cs = ContentChangeSet(
        id="cs.mixed",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="p1",
                collection="pages",
                item_id="id.1",
            ),
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="p2",
                collection="media",
                item_id="id.2",
            ),
        ),
    )
    result = router.apply(cs)
    assert result.success is True
    # Each provider receives only its own operation
    assert len(r1.calls) == 1
    assert r1.calls[0][0] == "apply"
    assert len(r2.calls) == 1
    assert r2.calls[0][0] == "apply"


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


class _CollectionAwareRepo(_RecordingRepo):
    """Repo whose ``get_by_id`` accepts a ``collection`` kwarg."""

    def get_by_id(self, item_id, *, include_drafts=False, collection=None):
        self.calls.append(("get_by_id", item_id, include_drafts, collection))
        for it in self._items:
            if it.id == item_id and (collection is None or it.collection == collection):
                return it
        return None


def test_item14_router_forwards_collection_when_supported():
    """Item 14: capability-detect ``collection`` kwarg and forward it."""
    reg = RepositoryRegistry()
    items = [
        _item(collection="pages", slug="home", id_="home"),
        _item(collection="posts", slug="home", id_="home"),
    ]
    repo = _CollectionAwareRepo("p", items=items)
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(default_provider="p"))
    got = router.get_by_id("home", collection="posts")
    assert got is not None
    assert got.collection == "posts"


def test_item14_router_falls_back_to_list_items_when_no_collection_param():
    """Item 14: repo without ``collection`` kwarg → fall back to list_items
    scoped to the requested collection, not a naive ``get_by_id`` on any
    collection (which could return a same-id item from a different one).
    """
    reg = RepositoryRegistry()
    # _RecordingRepo.get_by_id has NO ``collection`` kwarg. Two items share
    # the same ``id`` across different collections.
    items = [
        _item(collection="pages", slug="home", id_="home"),
        _item(collection="posts", slug="home", id_="home"),
    ]
    repo = _RecordingRepo("p", items=items)
    reg.register("p", repo)
    router = ContentRouter(reg, RouterConfig(default_provider="p"))
    got = router.get_by_id("home", collection="posts")
    assert got is not None
    assert got.collection == "posts"
    # The router must have called list_items on the requested collection.
    assert any(c[0] == "list_items" and c[1] == "posts" for c in repo.calls)


# ---------------------------------------------------------------------------
# Item 11: **kwargs-only repos are NOT treated as collection-aware
# ---------------------------------------------------------------------------


class _KwargsOnlyRepo:
    """A repo whose get_by_id accepts arbitrary kwargs but does NOT declare
    ``collection`` as an explicit parameter.

    Item 11: the router MUST NOT use this repo's get_by_id with a collection
    kwarg — it should fall back to list_items scoped to the requested
    collection.
    """

    def __init__(self, items):
        self._items = items
        self.calls = []

    def describe(self): ...
    def list_collections(self): return []
    def list_items(self, collection, *, include_drafts=False):
        self.calls.append(("list_items", collection, include_drafts))
        return [i for i in self._items if i.collection == collection]

    def get_by_id(self, item_id, **kwargs):
        # Never has an explicit `collection` param — the router must not
        # rely on **kwargs for capability detection.
        self.calls.append(("get_by_id", item_id, kwargs))
        return next((i for i in self._items if i.id == item_id), None)

    def get_by_slug(self, collection, slug, *, include_drafts=False):
        return next(
            (i for i in self._items if i.collection == collection and i.slug == slug),
            None,
        )

    def validate(self, item): ...
    def apply(self, changeset): ...
    def health(self): ...


def test_item11_kwargs_only_repo_uses_list_items_fallback():
    a = _item(collection="alpha", slug="s1", id_="shared-id")
    b = _item(collection="beta", slug="s2", id_="shared-id")
    repo = _KwargsOnlyRepo([a, b])
    registry = RepositoryRegistry()
    registry.register("p1", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="p1"))
    result = router.get_by_id("shared-id", collection="alpha", include_drafts=True)
    # Fallback should scan alpha only.
    assert result is not None
    assert result.collection == "alpha"
    # Router must have used list_items, NOT get_by_id with a collection kwarg.
    kinds = [c[0] for c in repo.calls]
    assert "list_items" in kinds
    for c in repo.calls:
        if c[0] == "get_by_id":
            # If get_by_id was called it must NOT have received a collection kwarg.
            assert "collection" not in c[2], c


class _ExplicitCollectionRepo:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def describe(self): ...
    def list_collections(self): return []
    def list_items(self, collection, *, include_drafts=False):
        return [i for i in self._items if i.collection == collection]

    def get_by_id(self, item_id, *, include_drafts=False, collection=""):
        self.calls.append(("get_by_id", item_id, include_drafts, collection))
        return next(
            (i for i in self._items if i.id == item_id and (not collection or i.collection == collection)),
            None,
        )

    def get_by_slug(self, collection, slug, *, include_drafts=False): ...
    def validate(self, item): ...
    def apply(self, changeset): ...
    def health(self): ...


def test_item11_explicit_collection_kwarg_detected():
    a = _item(collection="alpha", slug="s1", id_="id-a")
    b = _item(collection="beta", slug="s2", id_="id-b")
    repo = _ExplicitCollectionRepo([a, b])
    registry = RepositoryRegistry()
    registry.register("p1", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="p1"))
    router.get_by_id("id-a", collection="alpha", include_drafts=True)
    # Router must have delegated to repo.get_by_id with collection kwarg.
    assert repo.calls
    assert repo.calls[0] == ("get_by_id", "id-a", True, "alpha")


def test_item11_protocol_type_check():
    from cauldron_content.repository import CollectionAwareRepository
    aware = _ExplicitCollectionRepo([])
    kwargs_only = _KwargsOnlyRepo([])
    # runtime_checkable protocol: aware repo satisfies it; kwargs-only does not.
    assert isinstance(aware, CollectionAwareRepository)
    # kwargs-only structurally does have get_by_id, but it doesn't accept the
    # exact keyword signature. runtime_checkable protocols only check the
    # method's presence, so this is a structural fit — the router uses signature
    # inspection to distinguish them.
    assert callable(getattr(kwargs_only, "get_by_id", None))
