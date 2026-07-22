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
    # Simulate application writing v2; record_applied reads the file itself.
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.4")
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
    adapter.record_applied("cs.6")
    assert adapter.inspect("cs.6")["has_application_result"] is True


# ---------------------------------------------------------------------------
# Fix 4: Hash domain — record_applied stores raw SHA-256 from canonical files
# ---------------------------------------------------------------------------


def test_record_applied_stores_raw_file_hash(tmp_path):
    """record_applied() stores the raw SHA-256 of the canonical file after mutation."""
    import hashlib
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("original", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.rh1", operations=ops)
    adapter.prepare("cs.rh1", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.rh1")

    post_hashes = adapter.get_post_application_hashes("cs.rh1")
    expected = hashlib.sha256(b"modified").hexdigest()
    assert post_hashes.get("home") == expected


def test_record_applied_stores_empty_hash_for_delete(tmp_path):
    """record_applied() stores '' for items deleted by the changeset."""
    from cauldron_content.contracts import ContentOperation, ContentOperationKind, ContentStatus
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "gone.md"
    f.write_text("---\nid: gone\n---\nbody", encoding="utf-8")

    op = ContentOperation(
        kind=ContentOperationKind.DELETE,
        provider="flatfile", collection="pages", item_id="gone", slug="gone",
        status=ContentStatus.PUBLISHED,
    )
    changeset = ContentChangeSet(id="cs.rd1", operations=(op,))
    adapter.prepare("cs.rd1", changeset)
    f.unlink()
    adapter.record_applied("cs.rd1")

    post_hashes = adapter.get_post_application_hashes("cs.rd1")
    assert post_hashes.get("gone") == ""


def test_rollback_conflict_file_deleted_after_apply(tmp_path):
    """File present after apply, then deleted externally → conflict on rollback."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.cda", operations=ops)
    adapter.prepare("cs.cda", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.cda")  # records hash of "v2"

    # File deleted externally after application
    f.unlink()

    with pytest.raises(RollbackConflict):
        adapter.rollback("cs.cda")


def test_rollback_conflict_deleted_file_recreated(tmp_path):
    """File deleted by apply, then recreated externally → conflict on rollback."""
    from cauldron_content.contracts import ContentOperation, ContentOperationKind, ContentStatus
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "gone.md"
    f.write_text("---\nid: gone\n---\nbody", encoding="utf-8")

    op = ContentOperation(
        kind=ContentOperationKind.DELETE,
        provider="flatfile", collection="pages", item_id="gone", slug="gone",
        status=ContentStatus.PUBLISHED,
    )
    changeset = ContentChangeSet(id="cs.cdr", operations=(op,))
    adapter.prepare("cs.cdr", changeset)
    f.unlink()
    adapter.record_applied("cs.cdr")  # post_hash = "" for "gone"

    # File recreated externally after delete
    f.write_text("---\nid: gone\n---\nrecreated", encoding="utf-8")

    with pytest.raises(RollbackConflict):
        adapter.rollback("cs.cdr")


def test_rollback_delete_op_restores_file(tmp_path):
    """Rolling back a DELETE operation restores the original file."""
    from cauldron_content.contracts import ContentOperation, ContentOperationKind, ContentStatus
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "deleted.md"
    f.write_text("---\nid: deleted\n---\noriginal body", encoding="utf-8")

    op = ContentOperation(
        kind=ContentOperationKind.DELETE,
        provider="flatfile", collection="pages", item_id="deleted", slug="deleted",
        status=ContentStatus.PUBLISHED,
    )
    changeset = ContentChangeSet(id="cs.rbdel", operations=(op,))
    adapter.prepare("cs.rbdel", changeset)
    f.unlink()
    adapter.record_applied("cs.rbdel")

    # Rollback with force (no external changes since delete)
    adapter.rollback("cs.rbdel", force=True, is_superuser=True)
    assert f.exists()
    assert "original body" in f.read_text(encoding="utf-8")
