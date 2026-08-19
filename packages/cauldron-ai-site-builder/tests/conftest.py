"""Test configuration for cauldron-ai-site-builder acceptance tests."""
from pathlib import Path
import json
import pytest
from django.conf import settings


_MINIMAL_PAGE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "page",
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "body": {"type": "string"},
    },
    "additionalProperties": False,
}


def pytest_configure(config):
    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "cauldron_content_operations",
                "cauldron_ai_admin",
                "cauldron_ai_attachments",
                "cauldron_ai_web",
                "cauldron_ai_site_builder",
            ],
            SECRET_KEY="test-secret-key-site-builder",
            USE_TZ=True,
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        )


@pytest.fixture
def site_root(tmp_path: Path) -> Path:
    """Create a minimal FlatFile site layout."""
    site = tmp_path / "site"
    (site / "content" / "pages").mkdir(parents=True)
    (site / "schemas").mkdir(parents=True)
    schema_file = site / "schemas" / "page.schema.json"
    schema_file.write_text(json.dumps(_MINIMAL_PAGE_SCHEMA), encoding="utf-8")
    return site


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def content_service(site_root: Path, workspace_root: Path):
    """Real ContentOperationService wired to a FlatFile site — no mocks."""
    from cauldron_cms_flatfile.config import FlatFileCMSConfig
    from cauldron_cms_flatfile.repository import FlatFileRepository
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_workspace_flatfile.snapshots import SnapshotService
    from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter
    from cauldron_content.registry import RepositoryRegistry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, get_adapter,
    )

    repo = FlatFileRepository(FlatFileCMSConfig(site_root=site_root))
    registry = RepositoryRegistry()
    registry.register("flatfile", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="flatfile"))

    ws_cfg = WorkspaceConfig(workspace_root=workspace_root)
    workspace = ChangeSetStore(ws_cfg)
    snapshots = SnapshotService(ws_cfg)
    adapter = FlatFileReversibleMutationAdapter(ws_cfg, str(site_root / "content"))

    existing = get_adapter("flatfile")
    if existing is not None and existing is not adapter:
        unregister_adapter("flatfile")
    register_adapter("flatfile", adapter)

    svc = ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=ContentOperationsConfig(
            require_approval=False,
            max_operations_per_change_set=50,
        ),
        locks_dir=ws_cfg.locks_dir,
        required_reversible_providers=frozenset({"flatfile"}),
    )
    yield svc
    unregister_adapter("flatfile")


@pytest.fixture
def override_root(tmp_path: Path) -> Path:
    """Minimal override store directory that UIStyleChangeService can use."""
    root = tmp_path / "overrides"
    root.mkdir()
    return root


@pytest.fixture
def style_service(override_root: Path, settings):
    """Real UIStyleChangeService wired to a temp override store."""
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    from cauldron_ai_admin.style_service import UIStyleChangeService
    return UIStyleChangeService()
