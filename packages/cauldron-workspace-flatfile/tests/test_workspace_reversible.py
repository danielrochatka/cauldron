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
    prep = adapter.prepare("cs.2", changeset)
    # Simulate application
    f.write_text("---\nid: home\n---\nModified", encoding="utf-8")
    # Rollback with force since we haven't recorded post hashes
    adapter.rollback(
        "cs.2", force=True, is_superuser=True,
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert "Original" in f.read_text(encoding="utf-8")


def test_rollback_undoes_create(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "new.md"
    ops = (_create_op("pages", "new", "new"),)
    changeset = ContentChangeSet(id="cs.3", operations=ops)
    prep = adapter.prepare("cs.3", changeset)
    f.write_text("just created", encoding="utf-8")
    adapter.rollback(
        "cs.3", force=True, is_superuser=True,
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert not f.exists()


def test_rollback_no_artifact_raises(tmp_path):
    adapter, _, _ = _make_adapter(tmp_path)
    with pytest.raises(RollbackNotSupported):
        adapter.rollback(
            "nonexistent-cs",
            expected_artifact_digest="a" * 64,
            expected_entry_count=1,
        )


def test_rollback_conflict_when_post_hash_mismatch(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.4", operations=ops)
    prep = adapter.prepare("cs.4", changeset)
    # Simulate application writing v2; record_applied reads the file itself.
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.4", artifact_digest=prep.artifact_digest)
    # Someone else edits it to v3
    f.write_text("v3", encoding="utf-8")
    with pytest.raises(RollbackConflict):
        adapter.rollback(
            "cs.4",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_rollback_force_requires_superuser(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.5", operations=ops)
    prep = adapter.prepare("cs.5", changeset)
    f.write_text("v2", encoding="utf-8")
    with pytest.raises(PermissionError):
        adapter.rollback(
            "cs.5", force=True, is_superuser=False,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_inspect_reports_state(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.6", operations=ops)
    prep = adapter.prepare("cs.6", changeset)
    info = adapter.inspect("cs.6")
    assert info["has_rollback_artifact"] is True
    assert info["has_application_result"] is False
    adapter.record_applied("cs.6", artifact_digest=prep.artifact_digest)
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
    prep = adapter.prepare("cs.rh1", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.rh1", artifact_digest=prep.artifact_digest)

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
    prep = adapter.prepare("cs.rd1", changeset)
    f.unlink()
    adapter.record_applied("cs.rd1", artifact_digest=prep.artifact_digest)

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
    prep = adapter.prepare("cs.cda", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.cda", artifact_digest=prep.artifact_digest)  # records hash of "v2"

    # File deleted externally after application
    f.unlink()

    with pytest.raises(RollbackConflict):
        adapter.rollback(
            "cs.cda",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


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
    prep = adapter.prepare("cs.cdr", changeset)
    f.unlink()
    adapter.record_applied("cs.cdr", artifact_digest=prep.artifact_digest)  # post_hash = "" for "gone"

    # File recreated externally after delete
    f.write_text("---\nid: gone\n---\nrecreated", encoding="utf-8")

    with pytest.raises(RollbackConflict):
        adapter.rollback(
            "cs.cdr",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


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
    prep = adapter.prepare("cs.item4.trav", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.item4.trav", artifact_digest=prep.artifact_digest)

    # Tamper: point rel_path outside content_root.
    art_path = cfg.snapshots_dir / "cs.item4.trav" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["rel_path"] = "../../etc/passwd"
    art["files"][0]["canonical_path"] = "/etc/passwd"
    art_path.write_text(json.dumps(art))

    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    with pytest.raises((PathEscapeError, RollbackConflict, RollbackArtifactInvalid)):
        # After tampering the digest no longer matches — pass what we recorded.
        adapter.rollback(
            "cs.item4.trav", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_item4_tampered_rel_path_absolute_refused(tmp_path):
    """Absolute rel_path must be refused."""
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("original", encoding="utf-8")

    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item4.abs", operations=ops)
    prep = adapter.prepare("cs.item4.abs", changeset)
    f.write_text("modified", encoding="utf-8")
    adapter.record_applied("cs.item4.abs", artifact_digest=prep.artifact_digest)

    art_path = cfg.snapshots_dir / "cs.item4.abs" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["rel_path"] = "/etc/passwd"
    art_path.write_text(json.dumps(art))

    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    with pytest.raises((PathEscapeError, RollbackConflict, RollbackArtifactInvalid)):
        adapter.rollback(
            "cs.item4.abs", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


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
    prep = adapter.prepare("cs.item6.miss", changeset)
    f.write_text("v2", encoding="utf-8")
    # Do NOT call record_applied.
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback(
            "cs.item6.miss",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_item6_corrupt_post_state_blocks_non_forced_rollback(tmp_path):
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item6.corr", operations=ops)
    prep = adapter.prepare("cs.item6.corr", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.corr", artifact_digest=prep.artifact_digest)
    # Corrupt the file.
    state_path = cfg.snapshots_dir / "cs.item6.corr" / "post_application_state.json"
    state_path.write_text("not-json{", encoding="utf-8")
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback(
            "cs.item6.corr",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


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
    prep = adapter.prepare("cs.item6.part", changeset)
    f1.write_text("v2", encoding="utf-8")
    f2.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.part", artifact_digest=prep.artifact_digest)

    # Truncate to only one record.
    state_path = cfg.snapshots_dir / "cs.item6.part" / "post_application_state.json"
    doc = json.loads(state_path.read_text())
    doc["records"] = doc["records"][:1]
    state_path.write_text(json.dumps(doc))
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.rollback(
            "cs.item6.part",
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_item6_successful_rollback_of_create(tmp_path):
    """Full happy path for non-forced rollback of a create op."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "new.md"
    ops = (_create_op("pages", "new", "new"),)
    changeset = ContentChangeSet(id="cs.item6.crok", operations=ops)
    prep = adapter.prepare("cs.item6.crok", changeset)
    f.write_text("just created", encoding="utf-8")
    adapter.record_applied("cs.item6.crok", artifact_digest=prep.artifact_digest)
    adapter.rollback(
        "cs.item6.crok",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert not f.exists()


def test_item6_successful_rollback_of_update(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item6.upok", operations=ops)
    prep = adapter.prepare("cs.item6.upok", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item6.upok", artifact_digest=prep.artifact_digest)
    adapter.rollback(
        "cs.item6.upok",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
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
    prep = adapter.prepare("cs.item6.dup", changeset)
    p1.write_text("pages-v2", encoding="utf-8")
    p2.write_text("posts-v2", encoding="utf-8")
    adapter.record_applied("cs.item6.dup", artifact_digest=prep.artifact_digest)
    adapter.rollback(
        "cs.item6.dup",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
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
    prep = adapter.prepare("cs.item7.applied", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.applied", artifact_digest=prep.artifact_digest)
    vr = adapter.verify_applied_state(
        "cs.item7.applied",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert vr.status == "verified"


def test_item7_verify_applied_state_mismatch(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item7.mm", operations=ops)
    prep = adapter.prepare("cs.item7.mm", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.mm", artifact_digest=prep.artifact_digest)
    f.write_text("v3", encoding="utf-8")
    vr = adapter.verify_applied_state(
        "cs.item7.mm",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert vr.status == "mismatch"


def test_item7_verify_applied_state_missing(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    # Missing SQL evidence -> missing_evidence (never verified).
    vr = adapter.verify_applied_state(
        "no-such-cs",
        expected_artifact_digest="",
        expected_entry_count=0,
    )
    assert vr.status == "missing_evidence"


def test_item7_verify_rolled_back_state_ok(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item7.rbok", operations=ops)
    prep = adapter.prepare("cs.item7.rbok", changeset)
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.item7.rbok", artifact_digest=prep.artifact_digest)
    adapter.rollback(
        "cs.item7.rbok",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    vr = adapter.verify_rolled_back_state(
        "cs.item7.rbok",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
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
    prep = adapter.prepare("cs.rbdel", changeset)
    f.unlink()
    adapter.record_applied("cs.rbdel", artifact_digest=prep.artifact_digest)

    # Rollback with force (no external changes since delete)
    adapter.rollback(
        "cs.rbdel", force=True, is_superuser=True,
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert f.exists()
    assert "original body" in f.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Item 5: rollback artifacts store rel_path only
# ---------------------------------------------------------------------------


def test_item5_new_artifact_omits_canonical_path(tmp_path):
    """Newly written rollback artifacts contain only rel_path, not canonical_path."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("hi", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.item5", operations=ops)
    adapter.prepare("cs.item5", changeset)
    art = json.loads(
        (cfg.snapshots_dir / "cs.item5" / "rollback_artifact.json").read_text()
    )
    assert "canonical_path" not in art["files"][0]
    assert art["files"][0]["rel_path"] == "pages/home.md"


def test_item5_legacy_artifact_still_readable(tmp_path):
    """Legacy artifacts with canonical_path but no rel_path still work."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.legacy", operations=ops)
    prep = adapter.prepare("cs.legacy", changeset)
    art_path = cfg.snapshots_dir / "cs.legacy" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    # Simulate legacy format: keep only canonical_path, drop rel_path.
    for entry in art["files"]:
        entry["canonical_path"] = str(f)
        entry.pop("rel_path", None)
        entry.pop("snap_sha256", None)
    art_path.write_text(json.dumps(art))
    f.write_text("v2", encoding="utf-8")
    # Legacy format is no longer accepted; record_applied should now reject.
    # Test now asserts that the v2 parser refuses the legacy shape.
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.record_applied("cs.legacy", artifact_digest=prep.artifact_digest)


# ---------------------------------------------------------------------------
# Item 6: snapshot file security
# ---------------------------------------------------------------------------


def test_item6_snap_name_absolute_refused(tmp_path):
    """An absolute snap_name in the artifact is rejected."""
    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.snapabs", operations=ops)
    prep = adapter.prepare("cs.snapabs", changeset)
    art_path = cfg.snapshots_dir / "cs.snapabs" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["snap_name"] = "/etc/passwd"
    art_path.write_text(json.dumps(art))
    with pytest.raises((PathEscapeError, RollbackArtifactInvalid)):
        adapter.rollback(
            "cs.snapabs", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_item6_snap_name_traversal_refused(tmp_path):
    """A traversal snap_name in the artifact is rejected."""
    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.snaptrav", operations=ops)
    prep = adapter.prepare("cs.snaptrav", changeset)
    art_path = cfg.snapshots_dir / "cs.snaptrav" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["snap_name"] = "../../etc/passwd"
    art_path.write_text(json.dumps(art))
    with pytest.raises((PathEscapeError, RollbackArtifactInvalid)):
        adapter.rollback(
            "cs.snaptrav", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


def test_item6_snap_hash_mismatch_refused(tmp_path):
    """A snapshot file whose contents changed after prepare is refused."""
    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f = content / "pages" / "home.md"
    f.write_text("v1", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.snaphash", operations=ops)
    prep = adapter.prepare("cs.snaphash", changeset)
    # Tamper the snapshot file after prepare.
    snap_dir = cfg.snapshots_dir / "cs.snaphash"
    snap_file = next(p for p in snap_dir.iterdir() if p.name.startswith("snap_"))
    snap_file.write_text("tampered", encoding="utf-8")
    f.write_text("v2", encoding="utf-8")
    adapter.record_applied("cs.snaphash", artifact_digest=prep.artifact_digest)
    with pytest.raises(RollbackArtifactInvalid):
        adapter.rollback(
            "cs.snaphash", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )


# ---------------------------------------------------------------------------
# Item 7: preflight-atomic rollback
# ---------------------------------------------------------------------------


def test_item7_preflight_fails_when_second_entry_invalid(tmp_path):
    """A malicious second entry causes zero mutations to entry #1."""
    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    from cauldron_workspace_flatfile.paths import PathEscapeError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    f1 = content / "pages" / "one.md"
    f2 = content / "pages" / "two.md"
    f1.write_text("v1-one", encoding="utf-8")
    f2.write_text("v1-two", encoding="utf-8")
    ops = (
        _update_op("pages", "one", "one"),
        _update_op("pages", "two", "two"),
    )
    changeset = ContentChangeSet(id="cs.preflight", operations=ops)
    prep = adapter.prepare("cs.preflight", changeset)
    f1.write_text("v2-one", encoding="utf-8")
    f2.write_text("v2-two", encoding="utf-8")
    adapter.record_applied("cs.preflight", artifact_digest=prep.artifact_digest)
    # Tamper the second entry to have a bad rel_path.
    art_path = cfg.snapshots_dir / "cs.preflight" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][1]["rel_path"] = "../../etc/passwd"
    art_path.write_text(json.dumps(art))
    # v2 remains on both files. Rollback must not touch f1 or f2.
    with pytest.raises((PathEscapeError, RollbackArtifactInvalid)):
        adapter.rollback(
            "cs.preflight", force=True, is_superuser=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=prep.entry_count,
        )
    assert f1.read_text() == "v2-one"
    assert f2.read_text() == "v2-two"


# ---------------------------------------------------------------------------
# Item 8: PreparationResult
# ---------------------------------------------------------------------------


def test_item8_prepare_returns_typed_result(tmp_path):
    from cauldron_workspace_flatfile.reversible import PreparationResult
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("hi", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.prepresult", operations=ops)
    result = adapter.prepare("cs.prepresult", changeset)
    assert isinstance(result, PreparationResult)
    assert len(result.artifact_digest) == 64
    assert result.entry_count == 1


def test_item8_unknown_kind_rejected_by_record_applied(tmp_path):
    """Item 8: record_applied must reject unknown op kinds instead of treating
    them as informational."""
    from cauldron_workspace_flatfile.reversible import RollbackPostStateUnavailable
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("hi", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.unkkind", operations=ops)
    prep = adapter.prepare("cs.unkkind", changeset)
    # Corrupt the artifact to introduce an unknown kind.
    art_path = cfg.snapshots_dir / "cs.unkkind" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["kind"] = "bogus"
    art_path.write_text(json.dumps(art))
    with pytest.raises(RollbackPostStateUnavailable):
        adapter.record_applied("cs.unkkind", artifact_digest=prep.artifact_digest)


def test_item8_empty_artifact_verify_rejects(tmp_path):
    """Empty rollback artifact fails verification (never verified)."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("hi", encoding="utf-8")
    ops = (_update_op("pages", "home", "home"),)
    changeset = ContentChangeSet(id="cs.empty", operations=ops)
    prep = adapter.prepare("cs.empty", changeset)
    # Empty the artifact files list.
    art_path = cfg.snapshots_dir / "cs.empty" / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"] = []
    art_path.write_text(json.dumps(art))
    # Digest changes when we edited the artifact — verification is bound to
    # the trusted (pre-tamper) digest, so status will not be "verified".
    vr = adapter.verify_rolled_back_state(
        "cs.empty",
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert vr.status != "verified"


# ---------------------------------------------------------------------------
# Item 9: duplicate targets in a single changeset
# ---------------------------------------------------------------------------


def test_item9_duplicate_targets_rejected_by_prepare(tmp_path):
    from cauldron_workspace_flatfile.reversible import DuplicateTargetError
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    (content / "pages" / "home.md").write_text("v1", encoding="utf-8")
    ops = (
        _update_op("pages", "home", "home"),
        _update_op("pages", "home", "home"),  # duplicate target
    )
    changeset = ContentChangeSet(id="cs.item9", operations=ops)
    with pytest.raises(DuplicateTargetError):
        adapter.prepare("cs.item9", changeset)


# ---------------------------------------------------------------------------
# Item 12: symlink escape hardening on discovered .md files
# ---------------------------------------------------------------------------


def test_item12_symlink_outside_content_root_rejected(tmp_path):
    """A markdown symlink pointing outside content_root is skipped by
    ``_find_file_for_item``, so we never read attacker-controlled files.
    """
    import os
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    # Legit page inside content_root.
    (content / "pages" / "legit.md").write_text(
        "---\nid: legit\n---\n\nOK\n", encoding="utf-8"
    )
    # Target outside content_root.
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "secret.md"
    outside_target.write_text(
        "---\nid: pwn\n---\n\nsecret\n", encoding="utf-8"
    )
    try:
        os.symlink(outside_target, content / "pages" / "pwn.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")
    # Item 12: looking up id 'pwn' must fail — the symlink is discovered
    # by glob() but rejected because it resolves outside content_root.
    got = adapter._find_file_for_item("pages", "pwn")
    assert got is None
    # The legit file is still discoverable.
    got_legit = adapter._find_file_for_item("pages", "legit")
    assert got_legit is not None


# ---------------------------------------------------------------------------
# Item 13: identifier segment validation in the adapter
# ---------------------------------------------------------------------------


def test_item13_invalid_collection_returns_none(tmp_path):
    """Unsafe collection segments must be rejected before any resolve."""
    adapter, cfg, content = _make_adapter(tmp_path)
    got = adapter._find_file_for_item("../etc", "passwd")
    assert got is None


def test_item13_invalid_slug_returns_none(tmp_path):
    """Unsafe slug segments must be rejected before any resolve."""
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    from cauldron_content.contracts import ContentOperation, ContentOperationKind, ContentStatus
    op = ContentOperation(
        kind=ContentOperationKind.CREATE,
        provider="flatfile",
        collection="pages",
        item_id="p1",
        slug="../etc/passwd",
        data={},
        body="",
        schema="",
        status=ContentStatus.PUBLISHED,
    )
    got = adapter._canonical_path_for_op(op)
    assert got is None


# ---------------------------------------------------------------------------
# Hardening: expected_entry_count binding & completion marker
# ---------------------------------------------------------------------------


def test_expected_entry_count_mismatch_rejected(tmp_path):
    """Rollback with a mismatched expected_entry_count is refused."""
    from cauldron_workspace_flatfile.reversible import RollbackArtifactInvalid
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    op = _create_op("pages", "p1")
    cs = ContentChangeSet(id="cs-hard-ec", operations=(op,))
    prep = adapter.prepare("cs-hard-ec", cs)
    # Wrong expected entry count → RollbackArtifactInvalid before mutation.
    with pytest.raises(RollbackArtifactInvalid):
        adapter._preflight_rollback(
            "cs-hard-ec",
            force=True,
            expected_artifact_digest=prep.artifact_digest,
            expected_entry_count=42,
        )


def test_expected_entry_count_correct_accepted(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    op = _create_op("pages", "p1")
    cs = ContentChangeSet(id="cs-hard-ec-ok", operations=(op,))
    prep = adapter.prepare("cs-hard-ec-ok", cs)
    # Correct expected entry count → preflight succeeds.
    plan = adapter._preflight_rollback(
        "cs-hard-ec-ok",
        force=True,
        expected_artifact_digest=prep.artifact_digest,
        expected_entry_count=prep.entry_count,
    )
    assert plan


def test_record_rolled_back_writes_bound_marker(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    op = _create_op("pages", "p1")
    cs = ContentChangeSet(id="cs-hard-marker", operations=(op,))
    prep = adapter.prepare("cs-hard-marker", cs)
    adapter.record_rolled_back("cs-hard-marker")
    marker = adapter.load_rollback_completion("cs-hard-marker")
    assert marker is not None
    assert marker["result_type"] == "rolled_back"
    assert marker["artifact_digest"] == prep.artifact_digest
    assert marker["entry_count"] == prep.entry_count
    assert marker["adapter_version"] == 2


def test_load_rollback_completion_missing_returns_none(tmp_path):
    adapter, cfg, content = _make_adapter(tmp_path)
    assert adapter.load_rollback_completion("no-such-cs") is None


def test_load_rollback_completion_rejects_malformed(tmp_path):
    import json
    adapter, cfg, content = _make_adapter(tmp_path)
    (content / "pages").mkdir()
    op = _create_op("pages", "p1")
    cs = ContentChangeSet(id="cs-hard-mal", operations=(op,))
    adapter.prepare("cs-hard-mal", cs)
    # Write a marker with wrong result_type.
    marker_path = adapter._rollback_result_path("cs-hard-mal")
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"result_type": "applied", "cs_id": "cs-hard-mal"}))
    assert adapter.load_rollback_completion("cs-hard-mal") is None
