"""Tests for the ChangeSetStore."""
import json

import pytest

from cauldron_content.contracts import (
    ContentChangeSet,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
)
from cauldron_workspace_flatfile.config import WorkspaceConfig
from cauldron_workspace_flatfile.store import (
    ChangeSetState,
    ChangeSetStore,
    ChangeSetStoreError,
)


def _make_cs(cs_id="cs.1") -> ContentChangeSet:
    return ContentChangeSet(
        id=cs_id,
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="flatfile",
                collection="pages",
                item_id="page.new",
                slug="new",
                data={"title": "New"},
                body="body",
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
        author="alice",
        description="First",
    )


def test_create_and_get_state(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    assert store.get_state("cs.1") == ChangeSetState.PROPOSED


def test_persisted_files(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    cs_dir = cfg.change_sets_dir / "cs.1"
    manifest = json.loads((cs_dir / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads((cs_dir / "payload.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "cs.1"
    assert manifest["state"] == "proposed"
    assert len(payload["operations"]) == 1
    assert payload["operations"][0]["kind"] == "create"


def test_transition_valid(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    store.transition("cs.1", ChangeSetState.VALIDATED)
    assert store.get_state("cs.1") == ChangeSetState.VALIDATED
    store.transition("cs.1", ChangeSetState.APPLIED)
    assert store.get_state("cs.1") == ChangeSetState.APPLIED


def test_transition_invalid_raises(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    # PROPOSED -> APPLIED is not allowed (must pass through VALIDATED)
    with pytest.raises(ChangeSetStoreError):
        store.transition("cs.1", ChangeSetState.APPLIED)


def test_transition_terminal_state_blocks_further(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    store.transition("cs.1", ChangeSetState.REJECTED)
    with pytest.raises(ChangeSetStoreError):
        store.transition("cs.1", ChangeSetState.APPLIED)


def test_list_ids_sorted(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs("cs.b"))
    store.create(_make_cs("cs.a"))
    assert store.list_ids() == ["cs.a", "cs.b"]


def test_list_ids_when_missing(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    assert store.list_ids() == []


def test_save_result(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    store.save_result("cs.1", {"applied": 1})
    data = json.loads(
        (cfg.change_sets_dir / "cs.1" / "result.json").read_text(encoding="utf-8")
    )
    assert data == {"applied": 1}


def test_atomic_write_leaves_no_tmp(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path)
    store = ChangeSetStore(cfg)
    store.create(_make_cs())
    cs_dir = cfg.change_sets_dir / "cs.1"
    leftover = [p for p in cs_dir.iterdir() if p.suffix == ".tmp"]
    assert leftover == []
