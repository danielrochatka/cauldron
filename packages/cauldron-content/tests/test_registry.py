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


# --- idempotency ---

def test_same_instance_same_owner_is_idempotent():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r, owning_module="mod.a")
    reg.register("p", r, owning_module="mod.a")  # must not raise
    assert reg.get("p") is r
    assert reg.get_owning_module("p") == "mod.a"


def test_same_instance_no_owner_is_idempotent():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r)
    reg.register("p", r)  # must not raise
    assert reg.get("p") is r


# --- ownership ---

def test_owning_module_stored_correctly():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo(), owning_module="cauldron.cms.flatfile")
    assert reg.get_owning_module("p") == "cauldron.cms.flatfile"


def test_owning_module_defaults_to_empty():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo())
    assert reg.get_owning_module("p") == ""


def test_get_owning_module_unknown_returns_empty():
    reg = RepositoryRegistry()
    assert reg.get_owning_module("nonexistent") == ""


def test_reset_clears_owners():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo(), owning_module="mod.a")
    reg.reset()
    assert reg.get_owning_module("p") == ""


# --- conflict detection ---

def test_different_instance_same_name_raises():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo())
    with pytest.raises(RegistrationError, match="already registered"):
        reg.register("p", _StubRepo())


def test_different_instance_error_includes_existing_owner():
    reg = RepositoryRegistry()
    reg.register("p", _StubRepo(), owning_module="mod.a")
    with pytest.raises(RegistrationError) as exc_info:
        reg.register("p", _StubRepo(), owning_module="mod.b")
    assert "mod.a" in str(exc_info.value)
    assert "mod.b" in str(exc_info.value)


def test_same_instance_different_owner_raises():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r, owning_module="mod.a")
    with pytest.raises(RegistrationError, match="mod.a"):
        reg.register("p", r, owning_module="mod.b")


# --- backward compatibility ---

def test_get_still_returns_repository():
    reg = RepositoryRegistry()
    r = _StubRepo()
    reg.register("p", r, owning_module="mod.x")
    assert reg.get("p") is r


def test_names_unaffected_by_ownership():
    reg = RepositoryRegistry()
    reg.register("zeta", _StubRepo(), owning_module="mod.z")
    reg.register("alpha", _StubRepo(), owning_module="mod.a")
    assert reg.names() == ["alpha", "zeta"]
