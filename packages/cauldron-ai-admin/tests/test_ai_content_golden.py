"""Golden-path integration tests: AI content.create_proposal tool handler.

These tests exercise the complete AI content creation pipeline without mocks,
from the perspective of the AI tool handler:

  _handle_create_proposal → ContentOperationService.create_change_request
  → validate_change_request → apply_change_request

All content-layer implementations are real (no mocks):
  - FlatFileRepository        (cauldron-cms-flatfile)
  - ChangeSetStore            (cauldron-workspace-flatfile)
  - FlatFileReversibleMutationAdapter  (cauldron-workspace-flatfile)
  - ContentOperationService   (cauldron-content-operations)

The AI tool handler is called directly (no LLM involved) so the tests remain
deterministic and do not require network access.

These tests live in the AI admin module because they validate the module's
contract with the content pipeline, not generic pipeline behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Schema: use the monorepo canonical schema if available, else minimal fallback.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_user(username: str):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    u, _ = User.objects.get_or_create(username=username)
    u.is_superuser = True
    u.is_staff = True
    u.save()
    return u


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    """Minimal site tree with page schema and empty content directory."""
    s = tmp_path / "site"
    (s / "content" / "pages").mkdir(parents=True)
    (s / "schemas").mkdir(parents=True)
    schema_dest = s / "schemas" / "page.schema.json"
    if _CANONICAL_PAGE_SCHEMA.exists():
        import shutil
        shutil.copy2(_CANONICAL_PAGE_SCHEMA, schema_dest)
    else:
        schema_dest.write_text(json.dumps(_MINIMAL_PAGE_SCHEMA), encoding="utf-8")
    return s


@pytest.fixture()
def real_content_service(site: Path, tmp_path: Path):
    """Wire a real ContentOperationService with flat-file backends."""
    from cauldron_cms_flatfile.config import FlatFileCMSConfig
    from cauldron_cms_flatfile.repository import FlatFileRepository
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_workspace_flatfile.snapshots import SnapshotService
    from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter
    from cauldron_content_operations.reversible import (
        get_adapter, register_adapter, unregister_adapter,
    )
    from cauldron_content.registry import RepositoryRegistry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    content_root = site / "content"

    repo = FlatFileRepository(FlatFileCMSConfig(site_root=site))
    registry = RepositoryRegistry()
    registry.register("flatfile", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="flatfile"))

    ws_cfg = WorkspaceConfig(workspace_root=workspace_root)
    workspace = ChangeSetStore(ws_cfg)
    snapshots = SnapshotService(ws_cfg)

    adapter = FlatFileReversibleMutationAdapter(ws_cfg, str(content_root))
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


@pytest.fixture()
def ai_context(real_content_service):
    """AdminAIToolContext wired with the real content service."""
    from cauldron_ai_admin.tools import AdminAIToolContext

    user = _make_user("ai_golden_actor")
    return AdminAIToolContext(
        actor=user,
        run_id="golden-test-run-001",
        correlation_id="golden-test-corr-001",
        content_service=real_content_service,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_proposal_tool_succeeds_with_empty_item_id(site, ai_context):
    """content.create_proposal handler returns success for AI-style CREATE ops.

    The AI omits item_id for new pages; the handler must pass this through to
    the service without raising an error.
    """
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    operations = [
        {
            "kind": "create",
            "collection": "pages",
            "item_id": "",
            "slug": "home",
            "schema": "page",
            "status": "draft",
            "data": {"title": "Home"},
            "body": "# Home\n\nWelcome.\n",
        }
    ]
    result = _handle_create_proposal(ai_context, operations=operations)

    assert isinstance(result, AdminAIToolResult), (
        f"Expected AdminAIToolResult, got {type(result).__name__}: {result}"
    )
    assert result.success, f"Tool handler failed: {result}"
    assert result.data.get("cs_id"), "cs_id missing from result data"
    assert result.data.get("status") == "proposed"


@pytest.mark.django_db
def test_create_proposal_full_pipeline_reaches_applied(site, ai_context):
    """Full pipeline: AI proposes pages → validate → apply → files on disk."""
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_content_operations.lifecycle import LifecycleState

    slugs = ["home", "about", "contact"]
    operations = [
        {
            "kind": "create",
            "collection": "pages",
            "item_id": "",
            "slug": slug,
            "schema": "page",
            "status": "draft",
            "data": {"title": slug.title()},
            "body": f"# {slug.title()}\n\nContent.\n",
        }
        for slug in slugs
    ]

    # 1. AI tool handler proposes the change set.
    result = _handle_create_proposal(ai_context, operations=operations)
    assert isinstance(result, AdminAIToolResult) and result.success, (
        f"Proposal failed: {result}"
    )
    request_id = result.data["cs_id"]
    user = ai_context.actor
    svc = ai_context.content_service

    # 2. Validate.
    r_validate = svc.validate_change_request(request_id, user=user, expected_version=1)
    assert r_validate.ok, (
        f"validate failed: {r_validate.error} "
        f"issues: {r_validate.meta.get('validation_issues')}"
    )

    # 3. Apply.
    r_apply = svc.apply_change_request(
        request_id, user=user, expected_version=r_validate.request_version
    )
    assert r_apply.ok, f"apply failed: {r_apply.error}"
    assert r_apply.lifecycle_state == LifecycleState.APPLIED.value

    # 4. Pages are on disk with correct ids.
    pages_dir = site / "content" / "pages"
    for slug in slugs:
        page_file = pages_dir / f"{slug}.md"
        assert page_file.exists(), f"missing: {slug}.md"
        text = page_file.read_text(encoding="utf-8")
        assert f"id: {slug}" in text, f"wrong id in {slug}.md"


@pytest.mark.django_db
def test_create_proposal_does_not_raise_missing_item_id_for_creates(site, ai_context):
    """validate_change_request must not report missing_item_id for CREATE ops."""
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    result = _handle_create_proposal(
        ai_context,
        operations=[{
            "kind": "create",
            "collection": "pages",
            "item_id": "",
            "slug": "services",
            "schema": "page",
            "status": "draft",
            "data": {"title": "Services"},
            "body": "Services page.\n",
        }],
    )
    assert isinstance(result, AdminAIToolResult) and result.success

    user = ai_context.actor
    svc = ai_context.content_service
    r_validate = svc.validate_change_request(
        result.data["cs_id"], user=user, expected_version=1
    )
    issues = r_validate.meta.get("validation_issues", [])
    assert not any(i.get("code") == "missing_item_id" for i in issues), (
        f"missing_item_id must not be raised for CREATE ops: {issues}"
    )


@pytest.mark.django_db
def test_create_proposal_does_not_produce_reconciliation_required(site, ai_context):
    """apply must reach APPLIED, not RECONCILIATION_REQUIRED."""
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult
    from cauldron_content_operations.lifecycle import LifecycleState
    from cauldron_content_operations.models import ContentChangeRequest

    result = _handle_create_proposal(
        ai_context,
        operations=[{
            "kind": "create",
            "collection": "pages",
            "item_id": "",
            "slug": "faq",
            "schema": "page",
            "status": "draft",
            "data": {"title": "FAQ"},
            "body": "Frequently asked questions.\n",
        }],
    )
    assert isinstance(result, AdminAIToolResult) and result.success

    request_id = result.data["cs_id"]
    user = ai_context.actor
    svc = ai_context.content_service

    r_validate = svc.validate_change_request(request_id, user=user, expected_version=1)
    assert r_validate.ok

    r_apply = svc.apply_change_request(
        request_id, user=user, expected_version=r_validate.request_version
    )

    cr = ContentChangeRequest.objects.get(request_id=request_id)
    assert cr.lifecycle_state != LifecycleState.RECONCILIATION_REQUIRED.value, (
        f"RECONCILIATION_REQUIRED: {cr.last_error_code!r} / {cr.last_error_summary!r}"
    )
    assert r_apply.ok, f"apply not ok: {r_apply.error}"
