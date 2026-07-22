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


# ---------------------------------------------------------------------------
# Item 19: expanded integration tests
# ---------------------------------------------------------------------------


def _propose_and_approve(service, e2e_env, *, item_data=None, body=None):
    proposer = _make_user(f"prop_{id(service):x}", perms=_superuser_perms())
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
            "data": item_data or {"title": "Updated"},
            "body": body or "New body",
            "schema": "pages",
            "status": "published",
        }],
        provider_name="flatfile",
    )
    assert proposal.ok
    rid = proposal.request_id
    validator = _make_user(f"val_{id(service):x}", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    assert v.ok
    approver = _make_user(f"appr_{id(service):x}", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    assert a.ok
    return rid, a.request_version


def test_e2e_item1_approval_denied_on_tampering(e2e_env):
    """Item 1: tampering payload after validate but before approve → approval denied."""
    service = e2e_env["service"]
    ws_root = e2e_env["ws_root"]

    proposer = _make_user("i1prop", perms=_superuser_perms())
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
            "data": {"title": "Original Proposed"},
            "body": "Original body",
            "schema": "pages",
            "status": "published",
        }],
        provider_name="flatfile",
    )
    assert proposal.ok
    rid = proposal.request_id
    validator = _make_user("i1val", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    assert v.ok

    # Tamper payload.json between validate and approve.
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    payload_path = ws_root / "change-sets" / cr.workspace_changeset_id / "payload.json"
    payload = json.loads(payload_path.read_text())
    payload["operations"][0]["data"] = {"title": "TAMPERED"}
    payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    approver = _make_user("i1appr", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    assert not a.ok
    assert a.error.code == "workspace.payload_integrity_mismatch"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "validated"


def test_e2e_item2_route_change_blocks_apply(e2e_env):
    """Item 2: switching router configuration after approval blocks apply."""
    service = e2e_env["service"]
    rid, aver = _propose_and_approve(service, e2e_env)
    # Force resolve_provider to return a different provider.
    orig = service._router._resolve_provider
    def _drift(coll):
        return "wrong-provider"
    service._router._resolve_provider = _drift  # type: ignore
    service._router.resolve_provider = _drift  # type: ignore
    try:
        applier = _make_user("i2apply", is_superuser=True, perms=_superuser_perms())
        ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    finally:
        service._router._resolve_provider = orig  # type: ignore
        service._router.resolve_provider = orig  # type: ignore
    assert not ap.ok
    assert ap.error.code == "operations.provider_route_changed"


def test_e2e_item3_duplicate_id_across_collections(e2e_env):
    """Item 3: same item_id in ``pages`` and ``posts`` resolves per collection."""
    service = e2e_env["service"]
    content_dir = e2e_env["content_dir"]
    # Add a same-id item to posts.
    (content_dir / "posts").mkdir(exist_ok=True)
    (content_dir / "posts" / "home.md").write_text(
        "---\nid: home\nslug: home\nstatus: published\nschema: posts\ntitle: Post Home\n---\nPost body",
        encoding="utf-8",
    )
    # Add posts schema.
    schemas_dir = content_dir.parent / "schemas"
    (schemas_dir / "posts.schema.json").write_text(
        json.dumps({"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]})
    )
    proposer = _make_user("i3prop", perms=_superuser_perms())
    pages_item = service.get_item("home", "pages", user=proposer)
    posts_item = service.get_item("home", "posts", user=proposer)
    assert pages_item is not None
    assert posts_item is not None
    assert pages_item.collection == "pages"
    assert posts_item.collection == "posts"
    assert pages_item.body != posts_item.body


def test_e2e_item9_duplicate_targets_rejected(e2e_env):
    """Item 9: duplicate ops targeting one file rejected at proposal."""
    service = e2e_env["service"]
    proposer = _make_user("i9prop", perms=_superuser_perms())
    items = service.list_items("pages", user=proposer)
    original = next(it for it in items if it.id == "home")
    result = service.create_change_request(
        user=proposer,
        operations=[
            {
                "kind": "update", "collection": "pages", "item_id": "home",
                "slug": "home", "expected_hash": original.hash,
                "data": {"title": "A"}, "body": "b1", "schema": "pages", "status": "published",
            },
            {
                "kind": "update", "collection": "pages", "item_id": "home",
                "slug": "home", "expected_hash": original.hash,
                "data": {"title": "B"}, "body": "b2", "schema": "pages", "status": "published",
            },
        ],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.duplicate_target"


def test_e2e_item4_malicious_collection_traversal_rejected(e2e_env):
    """Item 4: apply of a traversal-collection proposal never escapes content_root."""
    service = e2e_env["service"]
    content_dir = e2e_env["content_dir"]
    proposer = _make_user("i4prop", perms=_superuser_perms())
    result = service.create_change_request(
        user=proposer,
        operations=[{
            "kind": "create", "collection": "../../etc", "item_id": "passwd",
            "slug": "passwd", "data": {"title": "P"}, "body": "b",
            "schema": "pages", "status": "published",
        }],
        provider_name="flatfile",
    )
    # Router permits proposal (default provider matches). Validation and apply
    # must both refuse to write outside content_dir. Under our defaults, the
    # traversal is caught at apply-time (never on disk).
    assert result.ok  # proposal accepted; hardening runs at apply time
    validator = _make_user("i4val", perms=_superuser_perms())
    v = service.validate_change_request(result.request_id, user=validator, expected_version=1)
    # Validation may pass since it uses safe path resolution too.
    # The absolute guarantee we care about: NO file written outside content_dir.
    approver = _make_user("i4appr", perms=_superuser_perms())
    if v.ok:
        a = service.approve_change_request(
            result.request_id, user=approver, expected_version=v.request_version,
        )
        if a.ok:
            applier = _make_user("i4apply", is_superuser=True, perms=_superuser_perms())
            ap = service.apply_change_request(
                result.request_id, user=applier, expected_version=a.request_version,
            )
            # If apply succeeded/failed, target file must live under content_dir.
            # Confirm nothing was written under /etc.
            assert not (content_dir / ".." / ".." / "etc" / "passwd.md").exists()


def test_e2e_item6_tampered_snap_name_rejected(e2e_env):
    """Item 6: a tampered snap_name in the artifact is refused at rollback."""
    service = e2e_env["service"]
    home_path = e2e_env["home_path"]
    ws_root = e2e_env["ws_root"]

    rid, aver = _propose_and_approve(service, e2e_env)
    applier = _make_user("i6apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    assert ap.ok

    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    snap_dir = ws_root / "snapshots" / cr.workspace_changeset_id
    art_path = snap_dir / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][0]["snap_name"] = "../../etc/passwd"
    art_path.write_text(json.dumps(art))

    rb = service.rollback_change_request(
        rid, user=applier, force=False, expected_version=cr.request_version,
    )
    assert not rb.ok


def test_e2e_item7_malicious_second_entry_no_partial_restore(e2e_env):
    """Item 7: malicious later rollback entry causes no partial restoration."""
    service = e2e_env["service"]
    content_dir = e2e_env["content_dir"]
    ws_root = e2e_env["ws_root"]

    # Add a second file to the pages collection so the changeset has two ops.
    other = content_dir / "pages" / "other.md"
    other.write_text(
        "---\nid: other\nslug: other\nstatus: published\nschema: pages\ntitle: Other\n---\nother body",
        encoding="utf-8",
    )
    proposer = _make_user("i7prop", perms=_superuser_perms())
    items = service.list_items("pages", user=proposer)
    home = next(it for it in items if it.id == "home")
    oth = next(it for it in items if it.id == "other")
    proposal = service.create_change_request(
        user=proposer,
        operations=[
            {"kind": "update", "collection": "pages", "item_id": "home",
             "slug": "home", "expected_hash": home.hash,
             "data": {"title": "New Home"}, "body": "new home body",
             "schema": "pages", "status": "published"},
            {"kind": "update", "collection": "pages", "item_id": "other",
             "slug": "other", "expected_hash": oth.hash,
             "data": {"title": "New Other"}, "body": "new other body",
             "schema": "pages", "status": "published"},
        ],
        provider_name="flatfile",
    )
    assert proposal.ok, proposal.error
    rid = proposal.request_id
    validator = _make_user("i7val", perms=_superuser_perms())
    v = service.validate_change_request(rid, user=validator, expected_version=1)
    assert v.ok
    approver = _make_user("i7appr", perms=_superuser_perms())
    a = service.approve_change_request(rid, user=approver, expected_version=v.request_version)
    assert a.ok
    applier = _make_user("i7apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=a.request_version)
    assert ap.ok

    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    # Content after apply:
    home_after = (content_dir / "pages" / "home.md").read_bytes()
    other_after = other.read_bytes()

    # Tamper the SECOND entry with traversal.
    art_path = ws_root / "snapshots" / cr.workspace_changeset_id / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"][1]["rel_path"] = "../../etc/passwd"
    art_path.write_text(json.dumps(art))

    rb = service.rollback_change_request(
        rid, user=applier, force=False, expected_version=cr.request_version,
    )
    assert not rb.ok
    # Home content unchanged (no partial restore).
    assert (content_dir / "pages" / "home.md").read_bytes() == home_after
    assert other.read_bytes() == other_after


def test_e2e_item8_empty_artifact_and_post_state_rejected(e2e_env):
    """Item 8: empty rollback artifact / post-state fail verification."""
    service = e2e_env["service"]
    ws_root = e2e_env["ws_root"]
    rid, aver = _propose_and_approve(service, e2e_env)
    applier = _make_user("i8apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    assert ap.ok

    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    art_path = ws_root / "snapshots" / cr.workspace_changeset_id / "rollback_artifact.json"
    art = json.loads(art_path.read_text())
    art["files"] = []
    art_path.write_text(json.dumps(art))

    from cauldron_content_operations.reversible import get_adapter
    adapter = get_adapter("flatfile")
    assert adapter is not None
    vr = adapter.verify_rolled_back_state(cr.workspace_changeset_id)
    assert vr.status != "verified"


def test_e2e_item10_app_result_persistence_failure_finalizes_via_recon(e2e_env, monkeypatch):
    """Item 10: workspace application_result write failure → reconciliation completes."""
    service = e2e_env["service"]
    ws = e2e_env["ws"]
    rid, aver = _propose_and_approve(service, e2e_env)
    # Break save_application_result so post-mutation persistence fails.
    original_save = ws.save_application_result
    def _boom(cs_id, result):
        raise IOError("simulated failure")
    monkeypatch.setattr(ws, "save_application_result", _boom)
    applier = _make_user("i10apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    assert not ap.ok
    assert ap.lifecycle_state == "reconciliation_required"
    # Restore, then reconcile.
    monkeypatch.setattr(ws, "save_application_result", original_save)
    # Manually save the missing application_result so reconciliation can finalize.
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)
    ws.save_application_result(cr.workspace_changeset_id, {"applied_count": 1, "correlation_id": "cx"})
    results = service.reconcile(user=applier, dry_run=False)
    matched = [r for r in results if r["request_id"] == rid]
    assert matched
    assert matched[0]["action"] == "finalize_applied"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "applied"


def test_e2e_item11_missing_adapter_verify_remains_recon(e2e_env):
    """Item 11: no adapter → RECONCILIATION_REQUIRED remains."""
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        unregister_adapter, register_adapter,
    )
    service = e2e_env["service"]
    adapter = e2e_env["adapter"]
    rid, aver = _propose_and_approve(service, e2e_env)
    applier = _make_user("i11apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    assert ap.ok

    # Force into RECONCILIATION_REQUIRED artificially.
    cr = ContentChangeRequest.objects.get(request_id=rid)
    cr.lifecycle_state = "reconciliation_required"
    cr.save()

    unregister_adapter("flatfile")
    try:
        results = service.reconcile(user=applier, dry_run=False)
    finally:
        register_adapter("flatfile", adapter)
    matched = [r for r in results if r["request_id"] == rid]
    assert matched
    assert matched[0]["action"] == "requires_manual_review"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "reconciliation_required"


def test_e2e_item16_workspace_transition_failure_does_not_break_apply(e2e_env, monkeypatch):
    """Item 16: workspace state-sync failures are logged but do not fail apply."""
    service = e2e_env["service"]
    ws = e2e_env["ws"]
    rid, aver = _propose_and_approve(service, e2e_env)
    # transition() fails only for APPLIED — validation/approval passed already.
    original_transition = ws.transition
    def _boom(cs_id, new_state):
        if getattr(new_state, "value", new_state) == "applied":
            raise RuntimeError("workspace unavailable")
        return original_transition(cs_id, new_state)
    monkeypatch.setattr(ws, "transition", _boom)
    applier = _make_user("i16apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    # Apply must still succeed even though workspace APPLIED transition failed.
    assert ap.ok
    assert ap.lifecycle_state == "applied"


def test_e2e_item19_rollback_result_persistence_failure_finalizes_via_recon(e2e_env, monkeypatch):
    """Item 19: workspace rollback_result write failure → reconciliation completes."""
    service = e2e_env["service"]
    ws = e2e_env["ws"]
    rid, aver = _propose_and_approve(service, e2e_env)
    applier = _make_user("i19apply", is_superuser=True, perms=_superuser_perms())
    ap = service.apply_change_request(rid, user=applier, expected_version=aver)
    assert ap.ok
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=rid)

    original_save = ws.save_rollback_result
    def _boom(cs_id, result):
        raise IOError("simulated rollback save failure")
    monkeypatch.setattr(ws, "save_rollback_result", _boom)
    rb = service.rollback_change_request(
        rid, user=applier, force=False, expected_version=cr.request_version,
    )
    assert not rb.ok
    assert rb.lifecycle_state == "reconciliation_required"
    monkeypatch.setattr(ws, "save_rollback_result", original_save)
    # After the "failure", the on-disk rollback already succeeded. Reconciliation
    # only needs the workspace result and verified state.
    cr = ContentChangeRequest.objects.get(request_id=rid)
    ws.save_rollback_result(cr.workspace_changeset_id, {"correlation_id": "cx"})
    results = service.reconcile(user=applier, dry_run=False)
    matched = [r for r in results if r["request_id"] == rid]
    assert matched
    assert matched[0]["action"] == "finalize_rolled_back"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "rolled_back"
