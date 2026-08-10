"""Real FlatFile coordinated-publication integration test.

Exercises SiteChangeSetService.publish() with real:
    - FlatFileRepository        (cauldron-cms-flatfile)
    - ChangeSetStore            (cauldron-workspace-flatfile)
    - SnapshotService           (cauldron-workspace-flatfile)
    - FlatFileReversibleMutationAdapter (cauldron-workspace-flatfile)
    - ContentOperationService   (cauldron-content-operations)
    - Real ContentChangeRequest rows (DB)

Only the Astro build subprocess is mocked — everything else is genuine so
the compensation path exercises real filesystem restoration through the
FlatFileReversibleMutationAdapter's snapshot/restore artifacts.

Two scenarios:
    1. Two-request publish; request A applies (mutating a real .md file);
       request B's apply is forced to fail. The publication service must
       compensate A and the on-disk file MUST be restored to its original
       content byte-for-byte.
    2. Compensation itself is forced to report ``verified=False``. The
       publication service must set ``requires_reconciliation=True`` and
       leave the FS state as-is (no restore).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MINIMAL_PAGE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "page",
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string", "maxLength": 500},
    },
    "additionalProperties": False,
}


def _actor(username: str = "integ-flat"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"is_superuser": True, "is_staff": True},
    )
    if not user.is_superuser:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    return user


def _make_site(tmp_path: Path) -> Path:
    """Create a minimal FlatFile site with pages content dir and page schema."""
    site = tmp_path / "site"
    (site / "content" / "pages").mkdir(parents=True)
    (site / "schemas").mkdir(parents=True)
    schema_dest = site / "schemas" / "page.schema.json"
    # Prefer the canonical monorepo schema if present, otherwise fall back to
    # the minimal embedded schema so the test is self-contained.
    _canonical = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "cauldron-app" / "schemas" / "page.schema.json"
    )
    if _canonical.exists():
        shutil.copy2(_canonical, schema_dest)
    else:
        schema_dest.write_text(json.dumps(_MINIMAL_PAGE_SCHEMA), encoding="utf-8")
    return site


def _make_real_service(site: Path, workspace_root: Path):
    """Build a real ContentOperationService with real FlatFile infrastructure."""
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
    existing = get_adapter("flatfile")
    if existing is not None and existing is not adapter:
        unregister_adapter("flatfile")
    register_adapter("flatfile", adapter)

    cfg = ContentOperationsConfig(
        require_approval=False,
        max_operations_per_change_set=50,
    )
    return ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=cfg,
        locks_dir=ws_cfg.locks_dir,
        required_reversible_providers=frozenset({"flatfile"}),
    )


def _make_mock_build_service(tmp_path: Path):
    """Return a mock SiteBuildService (no real Astro build)."""
    from cauldron_site_astro.config import SiteAstroConfig
    frontend = tmp_path / "frontend"
    frontend.mkdir(exist_ok=True)
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    previews = tmp_path / "previews"
    previews.mkdir(exist_ok=True)
    config = SiteAstroConfig(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
        theme_root="",  # step 5 skipped
        previews_root=str(previews),
    )
    svc = MagicMock()
    svc._config = config

    from cauldron_site_astro.service import BuildResult
    svc.build_preview.return_value = BuildResult(
        ok=True, pages_built=2, output_dir=str(output),
        error="", build_log="",
    )
    svc.promote_output_with_backup.return_value = tmp_path / "backup"
    svc.restore_output.return_value = None
    svc.discard_output_backup.return_value = None
    return svc


def _teardown_adapter():
    """Remove any leftover flatfile adapter registration between tests."""
    try:
        from cauldron_content_operations.reversible import unregister_adapter
        unregister_adapter("flatfile")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scenario 1: successful compensation restores the real markdown file
# ---------------------------------------------------------------------------


def test_flatfile_coordinated_publish_compensates_on_partial_failure(tmp_path):
    """Request A mutates a real .md file; B fails; A must be compensated.

    Verifies:
      - the on-disk markdown file returns to its original content
      - request A ends in ROLLED_BACK lifecycle state
      - publish_build_result.compensated is True and
        requires_reconciliation is False
      - inspect().retryable is False (A is terminal)
    """
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.results import (
        ChangeRequestResult, OperationError,
    )
    from cauldron_content_operations.lifecycle import LifecycleState
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    try:
        site = _make_site(tmp_path)
        page_a_file = site / "content" / "pages" / "page-a.md"
        original_body = (
            "---\n"
            "id: page-a\n"
            "slug: page-a\n"
            "schema: page\n"
            "status: published\n"
            "title: Original\n"
            "---\n"
            "\n"
            "# Original body\n"
        )
        page_a_file.write_text(original_body, encoding="utf-8")

        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()

        service = _make_real_service(site, workspace_root)
        actor = _actor("integ-flat-compensate")

        # Compute the expected_hash for update operations by asking the real
        # provider for the current on-disk state. Update ops require this so
        # optimistic locking rejects stale updates.
        _repo = service._router._registry.get("flatfile")
        page_a = _repo.get_by_id("page-a", collection="pages", include_drafts=True)
        assert page_a is not None, "page-a fixture must be readable by flatfile"
        expected_hash_a = page_a.hash

        # --- Request A: update page-a
        r_create_a = service.create_change_request(
            user=actor,
            operations=[{
                "kind": "update",
                "collection": "pages",
                "item_id": "page-a",
                "slug": "page-a",
                "schema": "page",
                "expected_hash": expected_hash_a,
                "data": {"title": "Updated by A"},
                "body": "# Updated body A\n",
            }],
            provider_name="flatfile",
        )
        assert r_create_a.ok, f"create A failed: {r_create_a.error}"

        r_val_a = service.validate_change_request(
            r_create_a.request_id, user=actor,
            expected_version=r_create_a.request_version,
        )
        assert r_val_a.ok, f"validate A failed: {getattr(r_val_a.error, 'message', '')}"

        # --- Request B: also update page-a (will be forced to fail on apply)
        r_create_b = service.create_change_request(
            user=actor,
            operations=[{
                "kind": "update",
                "collection": "pages",
                "item_id": "page-a",
                "slug": "page-a",
                "schema": "page",
                "expected_hash": expected_hash_a,
                "data": {"title": "Attempted by B"},
                "body": "# B body\n",
            }],
            provider_name="flatfile",
        )
        assert r_create_b.ok
        r_val_b = service.validate_change_request(
            r_create_b.request_id, user=actor,
            expected_version=r_create_b.request_version,
        )
        assert r_val_b.ok

        # SiteChangeSet with both requests
        cs = SiteChangeSet.objects.create(
            status=SiteChangeSet.DRAFT_READY,
            content_request_ids=[r_create_a.request_id, r_create_b.request_id],
            staged_theme_css="",
        )

        mock_build_svc = _make_mock_build_service(tmp_path)

        # Force request B's apply to fail BEFORE mutation (APPLY_FAILED).
        original_apply = service.apply_change_request

        def controlled_apply(request_id, *, user, expected_version):
            if request_id == r_create_b.request_id:
                # Update the DB so lifecycle reflects APPLY_FAILED.
                try:
                    cr_b = ContentChangeRequest.objects.get(request_id=request_id)
                    cr_b.lifecycle_state = LifecycleState.APPLY_FAILED.value
                    cr_b.save(update_fields=["lifecycle_state", "updated_at"])
                except Exception:
                    pass
                return ChangeRequestResult(
                    ok=False,
                    request_id=request_id,
                    lifecycle_state=LifecycleState.APPLY_FAILED.value,
                    error=OperationError(
                        "test.force_fail", "Forced failure for test",
                    ),
                )
            return original_apply(
                request_id, user=user, expected_version=expected_version,
            )

        service.apply_change_request = controlled_apply

        with patch(
            "cauldron_site_astro.publication_service.get_build_service",
            return_value=mock_build_svc,
        ), patch(
            "cauldron_site_astro.publication_service._get_content_operation_service",
            return_value=service,
        ), patch(
            "cauldron_site_astro.publication_service._get_require_approval",
            return_value=False,
        ):
            result = SiteChangeSetService().publish(
                actor=actor, change_set_id=str(cs.id),
            )

        # Publish must have failed and the SiteChangeSet marked as such.
        assert result.ok is False
        cs.refresh_from_db()
        assert cs.status == SiteChangeSet.PUBLISH_FAILED

        # Key assertion: the actual markdown file must be restored byte-for-byte.
        assert page_a_file.read_text(encoding="utf-8") == original_body, (
            "page-a.md was not restored to its original content after "
            "compensation."
        )

        # Request A: applied then rolled back.
        cr_a = ContentChangeRequest.objects.get(request_id=r_create_a.request_id)
        assert cr_a.lifecycle_state == LifecycleState.ROLLED_BACK.value

        # Request B: never applied — terminal APPLY_FAILED, PROPOSED, or
        # VALIDATED depending on internal bookkeeping. It must NOT be applied.
        cr_b = ContentChangeRequest.objects.get(request_id=r_create_b.request_id)
        assert cr_b.lifecycle_state in {
            LifecycleState.APPLY_FAILED.value,
            LifecycleState.PROPOSED.value,
            LifecycleState.VALIDATED.value,
        }

        pbr = cs.publish_build_result or {}
        assert pbr.get("compensated") is True, (
            f"expected compensated=True, got publish_build_result={pbr}"
        )
        assert pbr.get("requires_reconciliation") is False

        # A is terminal (rolled back) — retry is not possible.
        inspect_result = SiteChangeSetService().inspect(str(cs.id))
        assert inspect_result.retryable is False
    finally:
        _teardown_adapter()


# ---------------------------------------------------------------------------
# Scenario 2: compensation verification failure → requires_reconciliation
# ---------------------------------------------------------------------------


def test_flatfile_coordinated_publish_reconciliation_when_compensation_unverified(tmp_path):
    """Force ``compensate_for_publication_failure`` to return verified=False.

    Verifies:
      - publish_build_result.requires_reconciliation is True
      - publish_build_result.compensated is False
      - FS state is NOT restored (canonical is uncertain)
      - inspect().retryable is False
    """
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.results import (
        ChangeRequestResult, CompensationResult, OperationError,
    )
    from cauldron_content_operations.lifecycle import LifecycleState
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    try:
        site = _make_site(tmp_path)
        page_a_file = site / "content" / "pages" / "page-a.md"
        original_body = (
            "---\n"
            "id: page-a\n"
            "slug: page-a\n"
            "schema: page\n"
            "status: published\n"
            "title: Original\n"
            "---\n"
            "\n"
            "# Original body\n"
        )
        page_a_file.write_text(original_body, encoding="utf-8")

        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()

        service = _make_real_service(site, workspace_root)
        actor = _actor("integ-flat-reconcile")

        # Compute expected_hash for update ops.
        _repo = service._router._registry.get("flatfile")
        page_a = _repo.get_by_id("page-a", collection="pages", include_drafts=True)
        assert page_a is not None
        expected_hash_a = page_a.hash

        # Real A + real B
        r_create_a = service.create_change_request(
            user=actor,
            operations=[{
                "kind": "update",
                "collection": "pages",
                "item_id": "page-a",
                "slug": "page-a",
                "schema": "page",
                "expected_hash": expected_hash_a,
                "data": {"title": "Updated by A"},
                "body": "# Updated body A\n",
            }],
            provider_name="flatfile",
        )
        assert r_create_a.ok
        r_val_a = service.validate_change_request(
            r_create_a.request_id, user=actor,
            expected_version=r_create_a.request_version,
        )
        assert r_val_a.ok

        r_create_b = service.create_change_request(
            user=actor,
            operations=[{
                "kind": "update",
                "collection": "pages",
                "item_id": "page-a",
                "slug": "page-a",
                "schema": "page",
                "expected_hash": expected_hash_a,
                "data": {"title": "Attempted by B"},
                "body": "# B body\n",
            }],
            provider_name="flatfile",
        )
        assert r_create_b.ok
        r_val_b = service.validate_change_request(
            r_create_b.request_id, user=actor,
            expected_version=r_create_b.request_version,
        )
        assert r_val_b.ok

        cs = SiteChangeSet.objects.create(
            status=SiteChangeSet.DRAFT_READY,
            content_request_ids=[r_create_a.request_id, r_create_b.request_id],
            staged_theme_css="",
        )

        mock_build_svc = _make_mock_build_service(tmp_path)

        original_apply = service.apply_change_request

        def controlled_apply(request_id, *, user, expected_version):
            if request_id == r_create_b.request_id:
                try:
                    cr_b = ContentChangeRequest.objects.get(request_id=request_id)
                    cr_b.lifecycle_state = LifecycleState.APPLY_FAILED.value
                    cr_b.save(update_fields=["lifecycle_state", "updated_at"])
                except Exception:
                    pass
                return ChangeRequestResult(
                    ok=False,
                    request_id=request_id,
                    lifecycle_state=LifecycleState.APPLY_FAILED.value,
                    error=OperationError(
                        "test.force_fail", "Forced failure for test",
                    ),
                )
            return original_apply(
                request_id, user=user, expected_version=expected_version,
            )

        service.apply_change_request = controlled_apply

        # Force compensation to report verified=False. The FS state after A
        # applied will remain (A's changes on disk); the publication service
        # must NOT attempt to restore Astro output when canonical is uncertain.
        def controlled_compensation(request_id, *, user, expected_version):
            return CompensationResult(
                ok=False,
                request_id=request_id,
                verified=False,
                lifecycle_state=LifecycleState.RECONCILIATION_REQUIRED.value,
                error_code="test.unverified",
                error_message="forced verification failure",
            )

        service.compensate_for_publication_failure = controlled_compensation

        with patch(
            "cauldron_site_astro.publication_service.get_build_service",
            return_value=mock_build_svc,
        ), patch(
            "cauldron_site_astro.publication_service._get_content_operation_service",
            return_value=service,
        ), patch(
            "cauldron_site_astro.publication_service._get_require_approval",
            return_value=False,
        ):
            result = SiteChangeSetService().publish(
                actor=actor, change_set_id=str(cs.id),
            )

        assert result.ok is False
        cs.refresh_from_db()
        pbr = cs.publish_build_result or {}
        assert pbr.get("requires_reconciliation") is True, (
            f"expected requires_reconciliation=True, got publish_build_result={pbr}"
        )
        assert pbr.get("compensated") is False

        # Astro output restore must NOT have been attempted because canonical
        # state was not verified as clean.
        mock_build_svc.restore_output.assert_not_called()

        # inspect() must report the change set as non-retryable.
        inspect_result = SiteChangeSetService().inspect(str(cs.id))
        assert inspect_result.retryable is False
    finally:
        _teardown_adapter()
