"""Full-stack integration tests: AI CREATE proposal with empty item_id.

These tests exercise the complete pipeline without mocks:

  create_change_request → validate_change_request → apply_change_request

using real implementations of:
  - FlatFileRepository        (cauldron-cms-flatfile)
  - ChangeSetStore            (cauldron-workspace-flatfile)
  - FlatFileReversibleMutationAdapter  (cauldron-workspace-flatfile)
  - ContentOperationService   (cauldron-content-operations)

The critical scenario under test: the AI omits item_id="" on CREATE
operations because the item does not yet exist.  Bugs in any layer
(validate, _stage_operation, prepare, record_applied, parse_rollback_artifact)
will surface here rather than hiding behind mocks.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Attempt to locate the canonical page schema from the monorepo's cauldron-app
# reference implementation; fall back to a minimal embedded schema so these
# tests are self-contained even outside the monorepo layout.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CANONICAL_PAGE_SCHEMA = _REPO_ROOT / "cauldron-app" / "schemas" / "page.schema.json"

_MINIMAL_PAGE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "additionalProperties": False,
}


def _make_user(username: str = "integ_user"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    u, _ = User.objects.get_or_create(username=username)
    u.is_superuser = True
    u.is_staff = True
    u.save()
    return u


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    """Minimal real site tree with page schema and empty content dirs."""
    s = tmp_path / "site"
    (s / "content" / "pages").mkdir(parents=True)
    (s / "schemas").mkdir(parents=True)
    schema_dest = s / "schemas" / "page.schema.json"
    if _CANONICAL_PAGE_SCHEMA.exists():
        shutil.copy2(_CANONICAL_PAGE_SCHEMA, schema_dest)
    else:
        schema_dest.write_text(json.dumps(_MINIMAL_PAGE_SCHEMA), encoding="utf-8")
    return s


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    wr = tmp_path / "workspace"
    wr.mkdir(parents=True)
    return wr


@pytest.fixture()
def real_service(site: Path, workspace_root: Path):
    """Wire up a complete ContentOperationService with no mocks."""
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

    content_root = site / "content"
    repo = FlatFileRepository(FlatFileCMSConfig(site_root=site))

    registry = RepositoryRegistry()
    registry.register("flatfile", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="flatfile"))

    ws_cfg = WorkspaceConfig(workspace_root=workspace_root)
    workspace = ChangeSetStore(ws_cfg)
    snapshots = SnapshotService(ws_cfg)

    adapter = FlatFileReversibleMutationAdapter(ws_cfg, str(content_root))
    # Clean up any previous registration so tests don't interfere.
    existing = get_adapter("flatfile")
    if existing is not None and existing is not adapter:
        unregister_adapter("flatfile")
    register_adapter("flatfile", adapter)

    cfg = ContentOperationsConfig(
        require_approval=False,
        max_operations_per_change_set=50,
    )
    svc = ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=cfg,
        locks_dir=ws_cfg.locks_dir,
        required_reversible_providers=frozenset({"flatfile"}),
    )
    yield svc
    # Teardown: unregister so other test suites start clean.
    unregister_adapter("flatfile")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ops_without_item_id(*slugs: str) -> list[dict]:
    """Build CREATE operations the way the AI does: item_id absent / empty."""
    return [
        {
            "kind": "create",
            "collection": "pages",
            "item_id": "",       # AI omits this for new items
            "slug": slug,
            "schema": "page",
            "status": "draft",
            "data": {"title": f"Page {slug}"},
            "body": f"# {slug}\n\nAI-generated body.\n",
        }
        for slug in slugs
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_single_create_without_item_id_reaches_applied(site, real_service):
    """Full pipeline: propose → validate → apply writes the file and reaches APPLIED."""
    from cauldron_content_operations.lifecycle import LifecycleState

    user = _make_user("integ_single")
    ops = _ops_without_item_id("index")

    # 1. Propose
    r_create = real_service.create_change_request(
        user=user, operations=ops, provider_name="flatfile",
    )
    assert r_create.ok, f"create failed: {r_create.error}"

    # 2. Validate
    r_validate = real_service.validate_change_request(
        r_create.request_id, user=user, expected_version=1,
    )
    assert r_validate.ok, f"validate failed: {r_validate.error} | issues: {r_validate.meta.get('validation_issues')}"

    # 3. Apply
    r_apply = real_service.apply_change_request(
        r_create.request_id, user=user, expected_version=2,
    )
    assert r_apply.ok, f"apply failed: {r_apply.error}"
    assert r_apply.lifecycle_state == LifecycleState.APPLIED.value

    # 4. File exists on disk with correct content
    page_file = site / "content" / "pages" / "index.md"
    assert page_file.exists(), "flat file was not written"
    content = page_file.read_text(encoding="utf-8")
    assert "id: index" in content, f"id not set to slug; got:\n{content}"
    assert "title: Page index" in content


@pytest.mark.django_db
def test_multi_page_create_without_item_id_all_reach_applied(site, real_service):
    """Six CREATE ops in a single change set all land on disk at APPLIED."""
    from cauldron_content_operations.lifecycle import LifecycleState

    user = _make_user("integ_multi")
    slugs = ["home", "products", "about", "team", "blog", "contact"]
    ops = _ops_without_item_id(*slugs)

    r_create = real_service.create_change_request(
        user=user, operations=ops, provider_name="flatfile",
    )
    assert r_create.ok, f"create failed: {r_create.error}"

    r_validate = real_service.validate_change_request(
        r_create.request_id, user=user, expected_version=1,
    )
    assert r_validate.ok, (
        f"validate failed: {r_validate.error}\n"
        f"issues: {r_validate.meta.get('validation_issues')}"
    )

    r_apply = real_service.apply_change_request(
        r_create.request_id, user=user, expected_version=2,
    )
    assert r_apply.ok, f"apply failed: {r_apply.error}"
    assert r_apply.lifecycle_state == LifecycleState.APPLIED.value

    pages_dir = site / "content" / "pages"
    for slug in slugs:
        page_file = pages_dir / f"{slug}.md"
        assert page_file.exists(), f"missing: {slug}.md"
        text = page_file.read_text(encoding="utf-8")
        assert f"id: {slug}" in text, f"wrong id in {slug}.md:\n{text}"


@pytest.mark.django_db
def test_validate_does_not_raise_missing_item_id_for_create(site, real_service):
    """validate_change_request must not produce missing_item_id for CREATE ops."""
    user = _make_user("integ_val_no_id")
    ops = _ops_without_item_id("about")

    r_create = real_service.create_change_request(
        user=user, operations=ops, provider_name="flatfile",
    )
    assert r_create.ok

    r_validate = real_service.validate_change_request(
        r_create.request_id, user=user, expected_version=1,
    )
    issues = r_validate.meta.get("validation_issues", [])
    missing_id_issues = [i for i in issues if i.get("code") == "missing_item_id"]
    assert not missing_id_issues, (
        f"missing_item_id must not be raised for CREATE ops: {missing_id_issues}"
    )


@pytest.mark.django_db
def test_apply_does_not_produce_reconciliation_required(site, real_service):
    """apply must not reach RECONCILIATION_REQUIRED — that means record_applied failed."""
    from cauldron_content_operations.lifecycle import LifecycleState
    from cauldron_content_operations.models import ContentChangeRequest

    user = _make_user("integ_no_recon")
    ops = _ops_without_item_id("contact")

    r_create = real_service.create_change_request(
        user=user, operations=ops, provider_name="flatfile",
    )
    assert r_create.ok

    r_validate = real_service.validate_change_request(
        r_create.request_id, user=user, expected_version=1,
    )
    assert r_validate.ok, f"validate failed: {r_validate.meta.get('validation_issues')}"

    r_apply = real_service.apply_change_request(
        r_create.request_id, user=user, expected_version=2,
    )

    # Check the DB state to get a useful error message if reconciliation fired.
    cr = ContentChangeRequest.objects.get(request_id=r_create.request_id)
    assert cr.lifecycle_state != LifecycleState.RECONCILIATION_REQUIRED.value, (
        f"RECONCILIATION_REQUIRED: last_error={cr.last_error_code!r} "
        f"summary={cr.last_error_summary!r}"
    )
    assert r_apply.ok, f"apply not ok: {r_apply.error}"


@pytest.mark.django_db
def test_schema_validation_rejects_unknown_fields(site, real_service):
    """data fields not in the schema must fail validate (additionalProperties:false)."""
    user = _make_user("integ_bad_schema")
    ops = [
        {
            "kind": "create",
            "collection": "pages",
            "item_id": "",
            "slug": "bad-page",
            "schema": "page",
            "status": "draft",
            "data": {"title": "OK", "unknown_field": "should fail"},
            "body": "",
        }
    ]
    r_create = real_service.create_change_request(
        user=user, operations=ops, provider_name="flatfile",
    )
    assert r_create.ok

    r_validate = real_service.validate_change_request(
        r_create.request_id, user=user, expected_version=1,
    )
    assert not r_validate.ok, "validation should have failed on unknown_field"
    issues = r_validate.meta.get("validation_issues", [])
    assert any("unknown_field" in str(i) or "additional" in str(i).lower() for i in issues), (
        f"expected schema_validation_error for unknown_field; got: {issues}"
    )
