"""Tests for the newer public ChangeSetStore APIs."""
from __future__ import annotations

import pytest

from cauldron_content.contracts import (
    ContentChangeSet,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
)
from cauldron_workspace_flatfile.config import WorkspaceConfig
from cauldron_workspace_flatfile.store import ChangeSetStore, ChangeSetStoreError


def _make_store(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    return ChangeSetStore(cfg), cfg


def _op(collection: str, item_id: str, slug: str = "") -> ContentOperation:
    return ContentOperation(
        kind=ContentOperationKind.CREATE,
        provider="flatfile",
        collection=collection,
        item_id=item_id,
        slug=slug or item_id,
        data={"title": "Hello"},
        body="body",
        schema="pages",
        status=ContentStatus.PUBLISHED,
    )


def test_locks_dir_public_property(tmp_path):
    store, cfg = _make_store(tmp_path)
    assert store.locks_dir == cfg.locks_dir
    assert store.locks_dir.name == "locks"


def test_load_changeset_roundtrip(tmp_path):
    store, _ = _make_store(tmp_path)
    changeset = ContentChangeSet(
        id="cs.round",
        operations=(_op("pages", "home"),),
        author="alice",
        description="round-trip",
    )
    store.create(changeset)
    loaded = store.load_changeset("cs.round")
    assert loaded.id == "cs.round"
    assert loaded.author == "alice"
    assert loaded.description == "round-trip"
    assert len(loaded.operations) == 1
    assert loaded.operations[0].item_id == "home"
    assert loaded.operations[0].data == {"title": "Hello"}


def test_load_changeset_missing_raises(tmp_path):
    store, _ = _make_store(tmp_path)
    with pytest.raises(ChangeSetStoreError):
        store.load_changeset("nope")


def test_save_and_load_application_result(tmp_path):
    store, _ = _make_store(tmp_path)
    changeset = ContentChangeSet(id="cs.app", operations=(_op("pages", "home"),))
    store.create(changeset)
    store.save_application_result("cs.app", {"applied_count": 1})
    loaded = store.load_application_result("cs.app")
    assert loaded is not None
    assert loaded["result_type"] == "applied"
    assert loaded["applied_count"] == 1


def test_save_and_load_rollback_result(tmp_path):
    store, _ = _make_store(tmp_path)
    changeset = ContentChangeSet(id="cs.rb", operations=(_op("pages", "home"),))
    store.create(changeset)
    store.save_rollback_result("cs.rb", {"correlation_id": "abc"})
    loaded = store.load_rollback_result("cs.rb")
    assert loaded is not None
    assert loaded["result_type"] == "rolled_back"


def test_load_missing_results_return_none(tmp_path):
    store, _ = _make_store(tmp_path)
    assert store.load_application_result("cs.missing") is None
    assert store.load_rollback_result("cs.missing") is None
