"""Tests for FlatFileRepository read and apply behaviour."""
import shutil
from pathlib import Path

import pytest

from cauldron_cms_flatfile.config import FlatFileCMSConfig
from cauldron_cms_flatfile.repository import PROVIDER_NAME, FlatFileRepository
from cauldron_content.contracts import (
    ContentChangeSet,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
)


def _make_repo(site: Path) -> FlatFileRepository:
    return FlatFileRepository(FlatFileCMSConfig(site_root=site))


def test_list_collections(temp_site: Path):
    repo = _make_repo(temp_site)
    assert set(repo.list_collections()) == {"pages", "posts"}


def test_list_items_excludes_drafts_by_default(temp_site: Path):
    repo = _make_repo(temp_site)
    ids = {i.id for i in repo.list_items("pages")}
    assert "page.home" in ids
    assert "page.about" in ids
    assert "page.draft" not in ids


def test_list_items_include_drafts(temp_site: Path):
    repo = _make_repo(temp_site)
    ids = {i.id for i in repo.list_items("pages", include_drafts=True)}
    assert "page.draft" in ids


def test_get_by_id(temp_site: Path):
    repo = _make_repo(temp_site)
    item = repo.get_by_id("page.home")
    assert item is not None
    assert item.slug == "home"


def test_get_by_id_returns_none_for_draft_by_default(temp_site: Path):
    repo = _make_repo(temp_site)
    assert repo.get_by_id("page.draft") is None
    assert repo.get_by_id("page.draft", include_drafts=True) is not None


def test_get_by_slug(temp_site: Path):
    repo = _make_repo(temp_site)
    item = repo.get_by_slug("pages", "about")
    assert item is not None
    assert item.id == "page.about"


def test_duplicate_id_raises(temp_site: Path, parity_dir: Path):
    # Copy a duplicate ID setup manually into the temp site
    target = temp_site / "content" / "pages" / "dup.md"
    shutil.copy2(parity_dir / "pages" / "home.md", target)
    # target has same id as home.md
    repo = _make_repo(temp_site)
    with pytest.raises(ValueError):
        repo.list_items("pages")


def test_create_operation(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.1",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.new",
                slug="new",
                data={"title": "New Page", "description": "Fresh content."},
                body="# New Page\n\nBody here.\n",
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert result.success, result
    assert (temp_site / "content" / "pages" / "new.md").exists()
    fetched = repo.get_by_slug("pages", "new")
    assert fetched is not None
    assert fetched.data["title"] == "New Page"


def test_create_fails_when_file_exists(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.dup",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.newdup",
                slug="home",  # collides with existing home.md
                data={"title": "Dup", "description": "Dup"},
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    codes = {i.code for i in result.validation_errors}
    assert "already_exists" in codes


def test_create_invalid_slug(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.badslug",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.x",
                slug="../escape",
                data={"title": "T", "description": "D"},
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    codes = {i.code for i in result.validation_errors}
    assert "invalid_slug" in codes


def test_update_operation(temp_site: Path):
    repo = _make_repo(temp_site)
    existing = repo.get_by_id("page.home")
    assert existing is not None
    cs = ContentChangeSet(
        id="cs.upd",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.UPDATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.home",
                expected_hash=existing.hash,
                data={"title": "Home Updated"},
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert result.success, result
    fetched = repo.get_by_id("page.home")
    assert fetched is not None
    assert fetched.data["title"] == "Home Updated"
    # description preserved via merge
    assert fetched.data["description"] == "Welcome to the home page."


def test_update_stale_hash_conflict(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.stale",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.UPDATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.home",
                expected_hash="0" * 64,
                data={"title": "New"},
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    assert len(result.conflicts) == 1
    assert result.conflicts[0].item_id == "page.home"


def test_delete_operation(temp_site: Path):
    repo = _make_repo(temp_site)
    existing = repo.get_by_id("page.home")
    assert existing is not None
    cs = ContentChangeSet(
        id="cs.del",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.DELETE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.home",
                expected_hash=existing.hash,
            ),
        ),
    )
    result = repo.apply(cs)
    assert result.success
    assert not (temp_site / "content" / "pages" / "home.md").exists()


def test_delete_stale_hash_conflict(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.stale-del",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.DELETE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.home",
                expected_hash="0" * 64,
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    assert len(result.conflicts) == 1


def test_delete_missing_item_returns_error(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.missing",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.DELETE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.does-not-exist",
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    codes = {i.code for i in result.validation_errors}
    assert "not_found" in codes


def test_validation_failure_blocks_apply(temp_site: Path):
    repo = _make_repo(temp_site)
    cs = ContentChangeSet(
        id="cs.invalid",
        operations=(
            ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider=PROVIDER_NAME,
                collection="pages",
                item_id="page.newx",
                slug="newx",
                data={},  # missing required title/description
                schema="pages",
                status=ContentStatus.PUBLISHED,
            ),
        ),
    )
    result = repo.apply(cs)
    assert not result.success
    assert result.validation_errors


def test_health(temp_site: Path):
    repo = _make_repo(temp_site)
    h = repo.health()
    assert h.healthy


def test_health_missing_root(tmp_path: Path):
    repo = FlatFileRepository(FlatFileCMSConfig(site_root=tmp_path / "does-not-exist"))
    h = repo.health()
    assert not h.healthy
