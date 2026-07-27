"""Tests for CauldronCmsFlatfileConfig.ready() provider auto-registration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.test import override_settings

from cauldron_content.registry import registry
from cauldron_cms_flatfile.apps import _register_provider
from cauldron_cms_flatfile.repository import FlatFileRepository


@pytest.fixture(autouse=True)
def reset_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def site_root(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    return site


@pytest.fixture
def site_with_schema(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    schema_dir = site / "schemas"
    schema_dir.mkdir()
    (schema_dir / "pages.schema.json").write_text(
        json.dumps({"type": "object", "properties": {"title": {"type": "string"}}})
    )
    return site


class TestReady:
    def test_registers_flatfile_repository_when_site_root_configured(self, site_root):
        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {"site_root": str(site_root)},
        }):
            _register_provider()
            repo = registry.get("flatfile")
            assert isinstance(repo, FlatFileRepository)

    def test_no_op_when_module_not_in_cauldron_modules(self):
        with override_settings(CAULDRON_MODULES={"cauldron.content": {}}):
            _register_provider()
            assert registry.get("flatfile") is None

    def test_no_op_when_site_root_absent_from_module_config(self):
        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {},
        }):
            _register_provider()
            assert registry.get("flatfile") is None

    def test_no_op_when_cauldron_modules_not_set(self):
        with override_settings(CAULDRON_MODULES=None):
            _register_provider()
            assert registry.get("flatfile") is None

    def test_idempotent_on_repeated_calls(self, site_root):
        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {"site_root": str(site_root)},
        }):
            _register_provider()
            first_repo = registry.get("flatfile")
            _register_provider()
            assert registry.get("flatfile") is first_repo

    def test_invalid_site_root_raises_improperly_configured(self, tmp_path):
        from django.core.exceptions import ImproperlyConfigured

        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {
                "site_root": str(tmp_path / "site"),
                "content_root": "/absolute/not/allowed",
            },
        }):
            with pytest.raises(ImproperlyConfigured, match="misconfiguration"):
                _register_provider()


class TestFreshInstallWorkflow:
    def test_router_get_repo_succeeds_on_fresh_install(self, site_root):
        """registry.get("flatfile") returns a repo after ready() on fresh install."""
        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {"site_root": str(site_root)},
        }):
            _register_provider()
            assert registry.get("flatfile") is not None

    def test_fresh_install_create_validate_apply_persist(self, site_with_schema):
        """On a fresh install, content can be created, validated, applied, and persisted."""
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
            ContentStatus,
        )

        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {"site_root": str(site_with_schema)},
        }):
            _register_provider()
            repo = registry.get("flatfile")
            assert repo is not None

            op = ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="flatfile",
                collection="pages",
                item_id="page-welcome",
                slug="welcome",
                status=ContentStatus.PUBLISHED,
                schema="pages",
                data={"title": "Welcome"},
                body="# Welcome\n\nHello world.",
            )
            result = repo.apply(ContentChangeSet(id="cs-001", operations=(op,)))

            assert result.success, result.validation_errors
            assert len(result.applied) == 1
            assert result.applied[0].id == "page-welcome"

            persisted = site_with_schema / "content" / "pages" / "welcome.md"
            assert persisted.exists()

    def test_published_file_persists_across_service_reconstruction(self, site_with_schema):
        """Content applied in one process is readable by a fresh FlatFileRepository."""
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
            ContentStatus,
        )
        from cauldron_cms_flatfile.config import FlatFileCMSConfig

        cfg = FlatFileCMSConfig(site_root=site_with_schema)
        repo1 = FlatFileRepository(cfg)

        op = ContentOperation(
            kind=ContentOperationKind.CREATE,
            provider="flatfile",
            collection="pages",
            item_id="page-about",
            slug="about",
            status=ContentStatus.PUBLISHED,
            schema="pages",
            data={"title": "About"},
            body="About us.",
        )
        result = repo1.apply(ContentChangeSet(id="cs-002", operations=(op,)))
        assert result.success

        # Simulate service restart: new registry, new FlatFileRepository.
        with override_settings(CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.cms.flatfile": {"site_root": str(site_with_schema)},
        }):
            _register_provider()
            repo2 = registry.get("flatfile")
            items = repo2.list_items("pages")
            assert any(item.id == "page-about" for item in items)
