"""Tests for RepositoryRegistry."""
import pytest

from cauldron_content.registry import RegistrationError, RepositoryRegistry


class _StubRepo:
    def describe(self): ...
    def list_collections(self): return []
    def list_items(self, collection, *, include_drafts=False): return []
    def get_by_id(self, item_id, *, include_drafts=False): return None
    def get_by_slug(self, collection, slug, *, include_drafts=False): return None
    def validate(self, item): ...
    def apply(self, changeset): ...
    def health(self): ...


def test_register_and_get():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r)
    assert reg.get("p") is r


def test_get_unknown_returns_none():
    reg = RepositoryRegistry()
    assert reg.get("missing") is None


def test_duplicate_registration_raises():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo())
    with pytest.raises(RegistrationError):
        reg.register("p", _StubRepo())


def test_names_sorted():
    reg = RepositoryRegistry()
    reg.register("zeta", _StubRepo())
    reg.register("alpha", _StubRepo())
    assert reg.names() == ["alpha", "zeta"]


def test_snapshot_is_shallow_copy():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r)
    snap = reg.snapshot()
    snap["p2"] = _StubRepo()
    assert reg.names() == ["p"]


def test_reset_clears():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo())
    reg.reset()
    assert reg.names() == []
