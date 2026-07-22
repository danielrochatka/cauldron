"""Tests for the FlatFileReversibleMutationAdapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauldron_content.contracts import (
    ContentChangeSet,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
)
from cauldron_workspace_flatfile.config import WorkspaceConfig
from cauldron_workspace_flatfile.reversible import (
    FlatFileReversibleMutationAdapter,
    RollbackConflict,
    RollbackNotSupported,
)


def _make_adapter(tmp_path):
    ws = tmp_path / "ws"
    content = tmp_path / "content"
    content.mkdir()
    cfg = WorkspaceConfig(workspace_root=ws)
    return FlatFileReversibleMutationAdapter(cfg, content), cfg, content


def _create_op(collection: str, item_id: str, slug: str = "") -> ContentOperation:
    return ContentOperation(
        kind=ContentOperationKind.CREATE,
        provider="flatfile",
        collection=collection,
        item_id=item_id,
        slug=slug or item_id,
        data={},
        body="",
        schema="",
        status=ContentStatus.PUBLISHED,
    )


def _update_op(collection: str, item_id: str, slug: str = "") -> ContentOperation:
    return ContentOperation(
        kind=ContentOperationKind.UPDATE,
        provider="flatfile",
        collection=collection,
        item_id=item_id,
        slug=slug or item_id,
        data={},
        body="",
        schema="",
        status=ContentStatus.PUBLISHED,
    )


def test_supports_rollback(tmp_path):
    adapter, _, _ = _make_adapter(tmp_path)
    assert adapter.supports_rollback is True


def test_prepare_snapshots_existing_and_creates(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    existing = content / "pages" / "hello.md"
    existing.write_text("hello", encoding="utf-8")

    ops = (
        _create_op("pages", "new-item", "new-item"),
        _update_op("pages", "hello", "hello"),
    )
    changeset = ContentChangeSet(id="cs.1", operations=ops)
    adapter.prepare("cs.1", changeset)

    assert adapter.has_rollback_artifact("cs.1")
    art_data = json.loads(
        (cfg.snapshots_dir / "cs.1" / "rollback_artifact.json").read_text(encoding="utf-8")
    )
    assert len(art_data["files"]) == 2
    kinds = [e["kind"] for e in art_data["files"]]
    assert "create" in kinds
    assert "update" in kinds


def test_rollback_restores_pre_state_for_update(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("---\nid: home\n---\nOriginal", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.2", operations=ops)
    adapter.prepare("cs.2", changeset)
    # Simulate application
    f.write_text("---\nid: home\n---\nModified", encoding="utf-8")
    # Rollback with force since we haven't recorded post hashes
    adapter.rollback("cs.2", force=True, is_superuser=True)
    assert "Original" in f.read_text(encoding="utf-8")


def test_rollback_undoes_create(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "new.md"
    ops = (_create_op("pages", "new", "new"),)
    changeset = ContentChangeSet(id="cs.3", operations=ops)
    adapter.prepare("cs.3", changeset)
    f.write_text("just created", encoding="utf-8")
    adapter.rollback("cs.3", force=True, is_superuser=True)
    assert not f.exists()


def test_rollback_no_artifact_raises(tmp_path):
    adapter, _, _ = _make_adapter(tmp_path)
    with pytest.raises(RollbackNotSupported):
        adapter.rollback("nonexistent-cs")


def test_rollback_conflict_when_post_hash_mismatch(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.4", operations=ops)
    adapter.prepare("cs.4", changeset)
    # Simulate application writing v2
    f.write_text("v2", encoding="utf-8")
    import hashlib
    v2_hash = hashlib.sha256(b"v2").hexdigest()
    adapter.record_applied("cs.4", {"home": v2_hash})
    # Someone else edits it to v3
    f.write_text("v3", encoding="utf-8")
    with pytest.raises(RollbackConflict):
        adapter.rollback("cs.4")


def test_rollback_force_requires_superuser(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.5", operations=ops)
    adapter.prepare("cs.5", changeset)
    f.write_text("v2", encoding="utf-8")
    with pytest.raises(PermissionError):
        adapter.rollback("cs.5", force=True, is_superuser=False)


def test_inspect_reports_state(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.6", operations=ops)
    adapter.prepare("cs.6", changeset)
    info = adapter.inspect("cs.6")
    assert info["has_rollback_artifact"] is True
    assert info["has_application_result"] is False
    adapter.record_applied("cs.6", {"home": "abc"})
    assert adapter.inspect("cs.6")["has_application_result"] is True
