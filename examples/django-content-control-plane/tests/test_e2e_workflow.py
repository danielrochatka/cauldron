"""End-to-end workflow test using real repository / router / adapter components.

Exercises the full proposal → validate → approve → apply → rollback lifecycle
against a real FlatFileRepository, RepositoryRegistry, ContentRouter,
FlatFileReversibleMutationAdapter, and ChangeSetStore. Only the auth layer
(Django users) is stubbed via pytest-django's DB fixture.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


pytestmark = pytest.mark.django_db


HOME_MD = """---
id: home
slug: home
status: published
schema: pages
title: Original Home
---

# Original Home

This is the original body.
"""


def _make_user(username, is_superuser=False, perms=None):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="p")
    if is_superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    if perms:
        from django.contrib.auth.models import Permission
        for codename in perms:
            try:
                perm = Permission.objects.get(codename=codename)
            except Permission.DoesNotExist:
                continue
            user.user_permissions.add(perm)
        user.refresh_from_db()
    return user


@pytest.fixture
def e2e_env(tmp_path):
    """Build a real content root, workspace, router, registry, adapter."""
    from cauldron_cms_flatfile.config import FlatFileCMSConfig
    from cauldron_cms_flatfile.repository import FlatFileRepository, PROVIDER_NAME
    from cauldron_content.registry import RepositoryRegistry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_workspace_flatfile.reversible import (
        FlatFileReversibleMutationAdapter,
    )
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, get_adapter,
    )
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    site_root = tmp_path / "site"
    content_dir = site_root / "content"
    schemas_dir = site_root / "schemas"
    ws_root = tmp_path / "ws"

    (content_dir / "pages").mkdir(parents=True)
    (content_dir / "posts").mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "pages.schema.json").write_text(
        json.dumps({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        })
    )
    (content_dir / "pages" / "home.md").write_text(HOME_MD, encoding="utf-8")

    cms_cfg = FlatFileCMSConfig(site_root=site_root)
    repo = FlatFileRepository(cms_cfg)

    registry = RepositoryRegistry()
    registry.register(PROVIDER_NAME, repo)
    router = ContentRouter(
        registry,
        RouterConfig(default_provider=PROVIDER_NAME, collections={}),
    )

    ws_cfg = WorkspaceConfig(workspace_root=ws_root)
    ws = ChangeSetStore(ws_cfg)
    adapter = FlatFileReversibleMutationAdapter(ws_cfg, content_dir)

    # Ensure adapter registered.
    unregister_adapter("flatfile")
    register_adapter("flatfile", adapter)
    assert get_adapter("flatfile") is adapter

    cfg = ContentOperationsConfig(
        require_approval=True,
        allow_self_approval=False,
        max_operations_per_change_set=10,
    )
    service = ContentOperationService(
        router=router, workspace=ws, config=cfg,
        locks_dir=ws_cfg.locks_dir,
    )

    yield {
        "content_dir": content_dir,
        "ws_root": ws_root,
        "service": service,
        "adapter": adapter,
        "ws": ws,
        "home_path": content_dir / "pages" / "home.md",
        "original_bytes": (content_dir / "pages" / "home.md").read_bytes(),
    }
    unregister_adapter("flatfile")


def _superuser_perms():
    return [
        "view_published_content",
        "view_draft_content",
        "view_content_change_requests",
        "propose_content_changes",
        "validate_content_changes",
        "approve_content_changes",
        "reject_content_changes",
        "apply_content_changes",
        "rollback_content_changes",
        "view_content_audit",
    ]


def test_e2e_full_workflow(e2e_env):
    """proposal → validate → approve → apply → rollback with real components."""
    service = e2e_env["service"]
    home_path = e2e_env["home_path"]
    original_bytes = e2e_env["original_bytes"]

    # 1. List published content — should include "home".
    proposer = _make_user("e2e_proposer", perms=_superuser_perms())
    items = service.list_items("pages", user=proposer)
    ids = [it.id for it in items]
    assert "home" in ids
    original = next(it for it in items if it.id == "home")

    # 2. Create an update proposal on the real home page.
    proposal = service.create_change_request(
        user=proposer,
        operations=[{
            "kind": "update",
            "collection": "pages",
            "item_id": "home",
            "slug": "home",
            "expected_hash": original.hash,
            "data": {"title": "Updated Home"},
            "body": "# Updated Home\n\nUpdated body content.",
            "schema": "pages",
            "status": "published",
        }],
        provider_name="flatfile",
    )
    assert proposal.ok, proposal.error
    rid = proposal.request_id

    # 3. Validate through real schema validator.
    validator = _make_user("e2e_validator", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    assert v.ok, v.error
    assert v.lifecycle_state == "validated"

    # 4. Approve with a different user (self-approval blocked).
    approver = _make_user("e2e_approver", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    assert a.ok, a.error
    assert a.lifecycle_state == "approved"

    # 5. Apply.
    applier = _make_user("e2e_applier", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=a.request_version)
    assert ap.ok, ap.error
    assert ap.lifecycle_state == "applied"

    # 6. Confirm canonical file changed.
    modified_text = home_path.read_text(encoding="utf-8")
    assert "Updated Home" in modified_text
    assert home_path.read_bytes() != original_bytes

    # 7. Rollback without force.
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    rb = service.rollback_change_request(
        rid, user=applier, force=False, expected_version=cr.request_version,
    )
    assert rb.ok, rb.error
    assert rb.lifecycle_state == "rolled_back"

    # 8. Confirm exact original bytes restored.
    assert home_path.read_bytes() == original_bytes

    # 10. Confirm audit event sequence is monotonically increasing.
    events = service.get_audit_history(rid, user=applier)
    seqs = [e.sequence for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    # 11. Confirm workspace application and rollback artifacts exist.
    ws_root = e2e_env["ws_root"]
    cs_id = cr.workspace_changeset_id
    snap_dir = ws_root / "snapshots" / cs_id
    assert (snap_dir / "rollback_artifact.json").exists()
    assert (snap_dir / "post_application_state.json").exists()
    assert (ws_root / "change-sets" / cs_id / "application_result.json").exists()
    assert (ws_root / "change-sets" / cs_id / "rollback_result.json").exists()


def test_e2e_tampered_payload_blocks_apply(e2e_env):
    """Tampering payload.json after approval blocks apply with integrity error."""
    service = e2e_env["service"]
    home_path = e2e_env["home_path"]
    ws_root = e2e_env["ws_root"]

    proposer = _make_user("e2e_prop_tamp", perms=_superuser_perms())
    items = service.list_items("pages", user=proposer)
    original = next(it for it in items if it.id == "home")

    proposal = service.create_change_request(
        user=proposer,
        operations=[{
            "kind": "update",
            "collection": "pages",
            "item_id": "home",
            "slug": "home",
            "expected_hash": original.hash,
            "data": {"title": "Legit"},
            "body": "legit body",
            "schema": "pages",
            "status": "published",
        }],
        provider_name="flatfile",
    )
    assert proposal.ok
    rid = proposal.request_id

    validator = _make_user("e2e_val_tamp", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    assert v.ok

    approver = _make_user("e2e_appr_tamp", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    assert a.ok

    # Tamper with payload.json.
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    payload_path = ws_root / "change-sets" / cr.workspace_changeset_id / "payload.json"
    data = json.loads(payload_path.read_text())
    data["operations"][0]["data"] = {"title": "TAMPERED"}
    payload_path.write_text(json.dumps(data, indent=2, sort_keys=True))

    applier = _make_user("e2e_apply_tamp", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=a.request_version)
    assert not ap.ok
    assert ap.error.code == "workspace.payload_integrity_mismatch"


def test_e2e_modified_after_apply_blocks_rollback(e2e_env):
    """Modifying the canonical file after apply blocks non-forced rollback."""
    service = e2e_env["service"]
    home_path = e2e_env["home_path"]

    proposer = _make_user("e2e_prop_mod", perms=_superuser_perms())
    items = service.list_items("pages", user=proposer)
    original = next(it for it in items if it.id == "home")

    proposal = service.create_change_request(
        user=proposer,
        operations=[{
            "kind": "update",
            "collection": "pages",
            "item_id": "home",
            "slug": "home",
            "expected_hash": original.hash,
            "data": {"title": "Y"},
            "body": "body Y",
            "schema": "pages",
            "status": "published",
        }],
        provider_name="flatfile",
    )
    rid = proposal.request_id
    validator = _make_user("e2e_val_mod", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    approver = _make_user("e2e_appr_mod", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    applier = _make_user("e2e_apply_mod", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=a.request_version)
    assert ap.ok

    # Now externally modify the file.
    home_path.write_text("# Externally modified\n", encoding="utf-8")

    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    rb = service.rollback_change_request(
        rid, user=applier, force=False, expected_version=cr.request_version,
    )
    assert not rb.ok
    # The RollbackConflict → generic rollback.failed via service.
    assert rb.error.code in ("rollback.failed",)
