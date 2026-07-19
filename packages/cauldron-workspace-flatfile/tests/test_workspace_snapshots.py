"""Tests for the SnapshotService."""
import pytest

from cauldron_workspace_flatfile.config import WorkspaceConfig
from cauldron_workspace_flatfile.snapshots import SnapshotConflict, SnapshotService


def test_capture_saves_existing_files(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    f1 = site / "a.md"
    f1.write_text("original", encoding="utf-8")

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [f1])

    snap_file = cfg.snapshots_dir / "cs.1" / "0000_a.md"
    assert snap_file.exists()
    assert snap_file.read_text(encoding="utf-8") == "original"


def test_capture_records_missing_files(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    missing = site / "missing.md"

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [missing])

    manifest_path = cfg.snapshots_dir / "cs.1" / "snapshot.json"
    assert manifest_path.exists()
    import json
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["files"][0]
    assert entry["existed"] is False


def test_rollback_restores_existing_file(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    f1 = site / "a.md"
    f1.write_text("original", encoding="utf-8")

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [f1])
    # Simulate applying a change
    f1.write_text("modified", encoding="utf-8")
    svc.rollback("cs.1", force=True)
    assert f1.read_text(encoding="utf-8") == "original"


def test_rollback_removes_created_file(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    missing = site / "created.md"

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [missing])
    # Simulate a create
    missing.write_text("new", encoding="utf-8")
    svc.rollback("cs.1")
    assert not missing.exists()


def test_rollback_conflict_detected(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    f1 = site / "a.md"
    f1.write_text("original", encoding="utf-8")

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [f1])
    # External modification (not the change we snapshotted)
    f1.write_text("someone-else", encoding="utf-8")
    with pytest.raises(SnapshotConflict):
        svc.rollback("cs.1")


def test_capture_disambiguates_same_basename_in_different_dirs(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    pages = tmp_path / "site" / "pages"
    posts = tmp_path / "site" / "posts"
    pages.mkdir(parents=True)
    posts.mkdir(parents=True)
    f_pages = pages / "home.md"
    f_posts = posts / "home.md"
    f_pages.write_text("pages-original", encoding="utf-8")
    f_posts.write_text("posts-original", encoding="utf-8")

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [f_pages, f_posts])

    snap_dir = cfg.snapshots_dir / "cs.1"
    assert (snap_dir / "0000_home.md").read_text(encoding="utf-8") == "pages-original"
    assert (snap_dir / "0001_home.md").read_text(encoding="utf-8") == "posts-original"

    # Rollback must restore both to the correct paths
    f_pages.write_text("modified", encoding="utf-8")
    f_posts.write_text("modified", encoding="utf-8")
    svc.rollback("cs.1", force=True)
    assert f_pages.read_text(encoding="utf-8") == "pages-original"
    assert f_posts.read_text(encoding="utf-8") == "posts-original"


def test_rollback_force_overrides_conflict(tmp_path):
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    site = tmp_path / "site"
    site.mkdir()
    f1 = site / "a.md"
    f1.write_text("original", encoding="utf-8")

    svc = SnapshotService(cfg)
    svc.capture("cs.1", [f1])
    f1.write_text("someone-else", encoding="utf-8")
    svc.rollback("cs.1", force=True)
    assert f1.read_text(encoding="utf-8") == "original"
