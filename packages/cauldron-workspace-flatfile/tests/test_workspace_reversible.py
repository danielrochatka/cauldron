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


# ---------------------------------------------------------------------------
# Item 4: Safe rollback paths — tampered artifacts cannot escape content_root
# ---------------------------------------------------------------------------


def test_item4_tampered_rel_path_traversal_refused(tmp_path):
    """rel_path containing '..' must not be accepted during rollback."""
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("original", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item4.trav", operations=ops)
    adapter.prepare("cs.item4.trav", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.item4.trav")

    # Tamper: point rel_path outside content_root.
    art_path = cfg.snapshots_dir / "cs.item4.trav" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["rel_path"] = "../../etc/passwd"
    art["files"][0]["canonical_path"] = "/etc/passwd"
    art_path.write_text(json.dumps(art))

    with pytest.raises((PathEscapeError, RollbackConflict)):
        adapter.rollback("cs.item4.trav", force=True, is_superuser=True)


def test_item4_tampered_rel_path_absolute_refused(tmp_path):
    """Absolute rel_path must be refused."""
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("original", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item4.abs", operations=ops)
    adapter.prepare("cs.item4.abs", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.item4.abs")

    art_path = cfg.snapshots_dir / "cs.item4.abs" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["rel_path"] = "/etc/passwd"
    art_path.write_text(json.dumps(art))

    with pytest.raises((PathEscapeError, RollbackConflict)):
        adapter.rollback("cs.item4.abs", force=True, is_superuser=True)


def test_item4_prepare_stores_rel_path(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("hi", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item4.rel", operations=ops)
    adapter.prepare("cs.item4.rel", changeset)
    art = json.loads(
        (cfg.snapshots_dir / "cs.item4.rel" / "rollback_artifact.json").read_text()
    )
    assert art["files"][0]["rel_path"] == "pages/home.md"


# ---------------------------------------------------------------------------
# Item 6: Non-forced rollback requires post-application state
# ---------------------------------------------------------------------------


def test_item6_missing_post_state_blocks_non_forced_rollback(tmp_path):
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item6.miss", operations=ops)
    adapter.prepare("cs.item6.miss", changeset)
    f.write_text("v2", encoding="utf-8")
    # Do NOT call record_applied.
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback("cs.item6.miss")


def test_item6_corrupt_post_state_blocks_non_forced_rollback(tmp_path):
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item6.corr", operations=ops)
    adapter.prepare("cs.item6.corr", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.corr")
    # Corrupt the file.
    state_path = cfg.snapshots_dir / "cs.item6.corr" / "post_application_state.json"
    state_path.write_text("not-json{", encoding="utf-8")
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback("cs.item6.corr")


def test_item6_partial_post_state_blocks_non_forced_rollback(tmp_path):
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    from cauldron_content.contracts import ContentOperation, ContentOperationKind, ContentStatus
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f1 = content / "pages" / "one.md"
    f2 = content / "pages" / "two.md"
    f1.write_text("v1", encoding="utf-8")
    f2.write_text("v1", encoding="utf-8")
    ops = (
        _update_op("pages", "one", "one"),
        _update_op("pages", "two", "two"),
    )
    changeset = ContentChangeSet(id="cs.item6.part", operations=ops)
    adapter.prepare("cs.item6.part", changeset)
    f1.write_text("v2", encoding="utf-8")
    f2.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.part")

    # Truncate to only one record.
    state_path = cfg.snapshots_dir / "cs.item6.part" / "post_application_state.json"
    doc = json.loads(state_path.read_text())
    doc["records"] = doc["records"][:1]
    state_path.write_text(json.dumps(doc))
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback("cs.item6.part")


def test_item6_successful_rollback_of_create(tmp_path):
    """Full happy path for non-forced rollback of a create op."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "new.md"
    ops = (_create_op("pages", "new", "new"),)
    changeset = ContentChangeSet(id="cs.item6.crok", operations=ops)
    adapter.prepare("cs.item6.crok", changeset)
    f.write_text("just created", encoding="utf-8")
    adapter.record_applied("cs.item6.crok")
    adapter.rollback("cs.item6.crok")
    assert not f.exists()


def test_item6_successful_rollback_of_update(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item6.upok", operations=ops)
    adapter.prepare("cs.item6.upok", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.upok")
    adapter.rollback("cs.item6.upok")
    assert f.read_text() == "v1"


def test_item6_duplicate_item_ids_across_collections(tmp_path):
    """Same item_id in different collections is safe with the per-op state records."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "posts").mkdir()
    p1 = content / "pages" / "shared.md"
    p2 = content / "posts" / "shared.md"
    p1.write_text("pages-v1", encoding="utf-8")
    p2.write_text("posts-v1", encoding="utf-8")
    ops = (
        _update_op("pages", "shared", "shared"),
        _update_op("posts", "shared", "shared"),
    )
    changeset = ContentChangeSet(id="cs.item6.dup", operations=ops)
    adapter.prepare("cs.item6.dup", changeset)
    p1.write_text("pages-v2", encoding="utf-8")
    p2.write_text("posts-v2", encoding="utf-8")
    adapter.record_applied("cs.item6.dup")
    adapter.rollback("cs.item6.dup")
    assert p1.read_text() == "pages-v1"
    assert p2.read_text() == "posts-v1"


# ---------------------------------------------------------------------------
# Item 7: Provider verification methods
# ---------------------------------------------------------------------------


def test_item7_verify_applied_state_ok(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item7.applied", operations=ops)
    adapter.prepare("cs.item7.applied", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.applied")
    vr = adapter.verify_applied_state("cs.item7.applied")
    assert vr.status == "verified"


def test_item7_verify_applied_state_mismatch(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item7.mm", operations=ops)
    adapter.prepare("cs.item7.mm", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.mm")
    f.write_text("v3", encoding="utf-8")
    vr = adapter.verify_applied_state("cs.item7.mm")
    assert vr.status == "mismatch"


def test_item7_verify_applied_state_missing(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    vr = adapter.verify_applied_state("no-such-cs")
    assert vr.status == "missing_evidence"


def test_item7_verify_rolled_back_state_ok(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item7.rbok", operations=ops)
    adapter.prepare("cs.item7.rbok", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.rbok")
    adapter.rollback("cs.item7.rbok")
    vr = adapter.verify_rolled_back_state("cs.item7.rbok")
    assert vr.status == "verified"


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
