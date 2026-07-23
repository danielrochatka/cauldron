"""Tests for the hardening pass (Items 1-14)."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db


def _make_user(perms=None, is_superuser=False, username="hard-user"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="password")
    if is_superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    return user


def _make_service_with_ws(tmp_path):
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)
    return service, ws, router


# ---------------------------------------------------------------------------
# Item 12: identifier validator + create requires slug
# ---------------------------------------------------------------------------

def test_item12_shared_validator_used_across_packages():
    """The three packages import the SAME validate_identifier_segment."""
    from cauldron_content._identifiers import validate_identifier_segment as canonical
    from cauldron_content_operations._identifiers import validate_identifier_segment as ops
    from cauldron_workspace_flatfile._identifiers import validate_identifier_segment as ws
    assert canonical is ops
    assert canonical is ws


def test_item12_length_limit_enforced():
    from cauldron_content._identifiers import (
        MAX_IDENTIFIER_LENGTH,
        validate_identifier_segment,
    )
    validate_identifier_segment("a" * MAX_IDENTIFIER_LENGTH, "slug")
    with pytest.raises(ValueError):
        validate_identifier_segment("a" * (MAX_IDENTIFIER_LENGTH + 1), "slug")


def test_item12_unicode_control_chars_rejected():
    from cauldron_content._identifiers import validate_identifier_segment
    # Zero-width joiner U+200D is Cf category.
    with pytest.raises(ValueError):
        validate_identifier_segment("bad‍joiner", "slug")
    # DEL character.
    with pytest.raises(ValueError):
        validate_identifier_segment("bad\x7Fdel", "slug")
    # Ascii control.
    with pytest.raises(ValueError):
        validate_identifier_segment("bad\x03char", "slug")


def test_item12_create_without_slug_rejected(tmp_path):
    service, ws, router = _make_service_with_ws(tmp_path)
    user = _make_user(is_superuser=True, username="i12-slugless")
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.invalid_slug"


# ---------------------------------------------------------------------------
# Item 2: adapter version enforcement
# ---------------------------------------------------------------------------

def test_item2_register_rejects_version_1_adapter():
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, AdapterVersionMismatch,
    )
    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 1
    with pytest.raises(AdapterVersionMismatch):
        register_adapter("flatfile", adapter)


def test_item2_register_rejects_missing_version():
    from cauldron_content_operations.reversible import (
        register_adapter, AdapterVersionMismatch,
    )
    class _NoVersion:
        supports_rollback = True
    with pytest.raises(AdapterVersionMismatch):
        register_adapter("flatfile", _NoVersion())


def test_item2_adapter_fully_supports_rollback_requires_v2():
    from cauldron_content_operations.service import _adapter_fully_supports_rollback

    class _V1:
        supports_rollback = True
        reversible_adapter_version = 1

        def prepare(self, *a, **k): ...
        def record_applied(self, *a, **k): ...
        def record_rolled_back(self, *a, **k): ...
        def rollback(self, *a, **k): ...
        def has_rollback_artifact(self, *a, **k): ...
        def verify_applied_state(self, *a, **k): ...
        def verify_rolled_back_state(self, *a, **k): ...
        def inspect(self, *a, **k): ...
    assert _adapter_fully_supports_rollback(_V1()) is False


def test_item2_flatfile_adapter_declares_v2(tmp_path):
    from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    adapter = FlatFileReversibleMutationAdapter(cfg, tmp_path / "content")
    assert adapter.reversible_adapter_version == 2


# ---------------------------------------------------------------------------
# Item 3: preparation evidence gating
# ---------------------------------------------------------------------------

def _prep_test_setup(tmp_path, prep_evidence):
    """Create a service + CR ready to be applied, with adapter prep patched."""
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
        ApplyResult,
    )
    from cauldron_content_operations.service import (
        ContentOperationService, _compute_canonical_changeset_hash,
    )
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter,
    )
    from cauldron_content_operations.models import ContentChangeRequest

    user = _make_user(is_superuser=True, username=f"prep-{uuid.uuid4().hex[:6]}")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.supports_rollback = True
    mock_adapter.reversible_adapter_version = 2
    mock_adapter.prepare.return_value = prep_evidence

    register_adapter("flatfile", mock_adapter)

    cs_id = str(uuid.uuid4())
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="flatfile",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    payload_hash = _compute_canonical_changeset_hash(cs)

    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())

    workspace = MagicMock()
    workspace.load_changeset.return_value = cs
    workspace.locks_dir = str(locks_dir)

    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)
    request_id = str(uuid.uuid4())
    ContentChangeRequest.objects.create(
        request_id=request_id,
        workspace_changeset_id=cs_id,
        provider_name="flatfile",
        lifecycle_state="approved",
        request_version=1,
        payload_hash=payload_hash,
        created_by=user,
    )
    return service, user, request_id, mock_adapter, router, unregister_adapter


def test_item3_empty_digest_blocks_mutation(tmp_path):
    class _P:
        artifact_digest = ""
        entry_count = 1
    service, user, request_id, adapter, router, unregister = _prep_test_setup(
        tmp_path, _P(),
    )
    try:
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()
    finally:
        unregister("flatfile")


def test_item3_zero_entry_count_blocks_mutation(tmp_path):
    class _P:
        artifact_digest = "a" * 64
        entry_count = 0
    service, user, request_id, adapter, router, unregister = _prep_test_setup(
        tmp_path, _P(),
    )
    try:
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()
    finally:
        unregister("flatfile")


def test_item3_mismatched_entry_count_blocks_mutation(tmp_path):
    class _P:
        artifact_digest = "a" * 64
        entry_count = 5  # But changeset has only 1 op.
    service, user, request_id, adapter, router, unregister = _prep_test_setup(
        tmp_path, _P(),
    )
    try:
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()
    finally:
        unregister("flatfile")


# ---------------------------------------------------------------------------
# Item 4: rollback requires SQL-bound evidence
# ---------------------------------------------------------------------------

def _rollback_test_setup(tmp_path, *, rollback_artifact_digest, entry_count):
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter,
    )
    from cauldron_content_operations.models import ContentChangeRequest

    user = _make_user(is_superuser=True, username=f"rb-{uuid.uuid4().hex[:6]}")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 2
    adapter.has_rollback_artifact.return_value = True
    register_adapter("flatfile", adapter)

    router = MagicMock()
    workspace = MagicMock()
    workspace.locks_dir = str(locks_dir)

    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)
    request_id = str(uuid.uuid4())
    cr = ContentChangeRequest.objects.create(
        request_id=request_id,
        workspace_changeset_id=str(uuid.uuid4()),
        provider_name="flatfile",
        lifecycle_state="applied",
        request_version=1,
        payload_hash="a" * 64,
        rollback_artifact_digest=rollback_artifact_digest,
        metadata={"rollback_artifact_entry_count": entry_count} if entry_count is not None else {},
        created_by=user,
    )
    return service, user, request_id, adapter, unregister_adapter


def test_item4_missing_sql_digest_blocks_rollback(tmp_path):
    service, user, request_id, adapter, unregister = _rollback_test_setup(
        tmp_path, rollback_artifact_digest="", entry_count=1,
    )
    try:
        result = service.rollback_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "rollback.evidence_unavailable"
        adapter.rollback.assert_not_called()
    finally:
        unregister("flatfile")


def test_item4_missing_sql_entry_count_blocks_rollback(tmp_path):
    service, user, request_id, adapter, unregister = _rollback_test_setup(
        tmp_path, rollback_artifact_digest="a" * 64, entry_count=None,
    )
    try:
        result = service.rollback_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "rollback.evidence_unavailable"
        adapter.rollback.assert_not_called()
    finally:
        unregister("flatfile")


def test_item4_zero_entry_count_blocks_rollback(tmp_path):
    service, user, request_id, adapter, unregister = _rollback_test_setup(
        tmp_path, rollback_artifact_digest="a" * 64, entry_count=0,
    )
    try:
        result = service.rollback_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "rollback.evidence_unavailable"
        adapter.rollback.assert_not_called()
    finally:
        unregister("flatfile")


# ---------------------------------------------------------------------------
# Item 1: reconciliation fails closed on every payload/workspace error
# ---------------------------------------------------------------------------

def test_item1_missing_payload_hash_cannot_reconcile_to_applied(tmp_path):
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="i1-missing-hash")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        request_id="rid-i1-mh",
        workspace_changeset_id="cs-i1-mh",
        provider_name="flatfile",
        lifecycle_state="applying",
        payload_hash="",  # MISSING
        rollback_artifact_digest="a" * 64,
        metadata={"rollback_artifact_entry_count": 1, "application_completed": True},
    )
    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 2
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    adapter.verify_rolled_back_state.return_value = VerificationResult(status="missing_evidence")
    adapter.load_rollback_completion.return_value = None
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-i1-mh"]
    assert matched
    cr.refresh_from_db()
    assert cr.lifecycle_state != "applied"
    assert cr.lifecycle_state == "reconciliation_required"


def test_item1_workspace_load_failure_cannot_reconcile_to_applied(tmp_path):
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )

    user = _make_user(is_superuser=True, username="i1-load-fail")
    ws = MagicMock()
    # load_changeset raises → workspace.load_failed
    ws.load_changeset.side_effect = RuntimeError("disk missing")
    ws.load_application_result.return_value = None
    ws.load_rollback_result.return_value = None
    locks = tmp_path / "locks"
    locks.mkdir()
    ws.locks_dir = str(locks)
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        request_id="rid-i1-lf",
        workspace_changeset_id="cs-i1-lf",
        provider_name="flatfile",
        lifecycle_state="applying",
        payload_hash="a" * 64,
        rollback_artifact_digest="a" * 64,
        metadata={"rollback_artifact_entry_count": 1, "application_completed": True},
    )
    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 2
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    adapter.verify_rolled_back_state.return_value = VerificationResult(status="missing_evidence")
    adapter.load_rollback_completion.return_value = None
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-i1-lf"]
    assert matched
    cr.refresh_from_db()
    assert cr.lifecycle_state != "applied"


def test_item1_dry_run_and_mutating_agree_on_missing_hash(tmp_path):
    """Dry-run + mutating produce the same decision on payload_hash absence."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="i1-dryrun")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)
    cr = ContentChangeRequest.objects.create(
        request_id="rid-i1-dr",
        workspace_changeset_id="cs-i1-dr",
        provider_name="flatfile",
        lifecycle_state="applying",
        payload_hash="",
        rollback_artifact_digest="a" * 64,
        metadata={"rollback_artifact_entry_count": 1, "application_completed": True},
    )
    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 2
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    adapter.verify_rolled_back_state.return_value = VerificationResult(status="missing_evidence")
    adapter.load_rollback_completion.return_value = None
    register_adapter("flatfile", adapter)
    try:
        dr = service.reconcile(user=user, dry_run=True)
        mutating = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")
    m1 = [r for r in dr if r["request_id"] == "rid-i1-dr"][0]
    m2 = [r for r in mutating if r["request_id"] == "rid-i1-dr"][0]
    assert m1["action"] != "would_finalize_applied"
    assert m2["action"] != "finalize_applied"


# ---------------------------------------------------------------------------
# Item 9: Admin rollback permission
# ---------------------------------------------------------------------------

def test_item9_rollback_button_uses_rollback_permission():
    """The rendered template checks perms.cauldron_content_operations.rollback_content_changes."""
    from pathlib import Path
    template = (
        Path(__file__).resolve().parents[2]
        / "cauldron-admin-content"
        / "src"
        / "cauldron_admin_content"
        / "templates"
        / "admin"
        / "cauldron_content_operations"
        / "contentchangerequest"
        / "change_form.html"
    )
    text = template.read_text(encoding="utf-8")
    # Rollback branch must gate on rollback_content_changes, not apply_content_changes.
    idx = text.find('original.lifecycle_state == "applied"')
    assert idx > 0
    tail = text[idx: idx + 500]
    assert "perms.cauldron_content_operations.rollback_content_changes" in tail
    assert "perms.cauldron_content_operations.apply_content_changes" not in tail


# ---------------------------------------------------------------------------
# Item 7: provider completion marker
# ---------------------------------------------------------------------------

def test_item7_rollback_completion_marker_format(tmp_path):
    """FlatFileReversibleMutationAdapter writes marker with all fields."""
    import json
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter

    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True)
    (content_root / "pages").mkdir()
    adapter = FlatFileReversibleMutationAdapter(cfg, content_root)

    cs_id = "cs-i7"
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="flatfile",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    prep = adapter.prepare(cs_id, cs)
    adapter.record_rolled_back(cs_id)
    marker_path = adapter._rollback_result_path(cs_id)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["result_type"] == "rolled_back"
    assert marker["cs_id"] == cs_id
    assert marker["artifact_digest"] == prep.artifact_digest
    assert marker["entry_count"] == prep.entry_count
    assert marker["adapter_version"] == 2


def test_item7_load_rollback_completion_returns_dict(tmp_path):
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter

    cfg = WorkspaceConfig(workspace_root=tmp_path / "ws")
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True)
    (content_root / "pages").mkdir()
    adapter = FlatFileReversibleMutationAdapter(cfg, content_root)
    cs_id = "cs-i7b"
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="flatfile",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    adapter.prepare(cs_id, cs)
    adapter.record_rolled_back(cs_id)
    marker = adapter.load_rollback_completion(cs_id)
    assert marker is not None
    assert marker["adapter_version"] == 2
    assert adapter.load_rollback_completion("no-such-cs") is None


# ---------------------------------------------------------------------------
# Item 8: workspace sync must be repaired before reconciliation finalizes
# ---------------------------------------------------------------------------

def test_item8_workspace_sync_must_be_repaired(tmp_path):
    """Reconciliation cannot restore SQL to applied while workspace sync fails."""
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
    )
    from cauldron_content_operations.service import (
        ContentOperationService, _compute_canonical_changeset_hash,
    )
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="i8-syncfail")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cs_id = "cs-i8-fail"
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="flatfile",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    ws.create(cs)
    # DO NOT advance workspace state — so PROPOSED -> APPLIED will fail.
    payload_hash = _compute_canonical_changeset_hash(cs)
    cr = ContentChangeRequest.objects.create(
        request_id="rid-i8-fail",
        workspace_changeset_id=cs_id,
        provider_name="flatfile",
        lifecycle_state="applying",
        payload_hash=payload_hash,
        rollback_artifact_digest="a" * 64,
        metadata={"rollback_artifact_entry_count": 1, "application_completed": True},
    )
    ws.save_application_result(cs_id, {"applied_count": 1, "correlation_id": "c1"})

    adapter = MagicMock()
    adapter.supports_rollback = True
    adapter.reversible_adapter_version = 2
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    adapter.verify_rolled_back_state.return_value = VerificationResult(status="missing_evidence")
    adapter.load_rollback_completion.return_value = None
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-i8-fail"]
    assert matched
    cr.refresh_from_db()
    assert cr.lifecycle_state != "applied"


# ---------------------------------------------------------------------------
# Frozen contract pass: adapter contract regression tests
# ---------------------------------------------------------------------------


def test_frozen_incompatible_registered_adapter_rejects_apply(tmp_path):
    """Item 1: an adapter that's registered but missing a required method
    must yield application.rollback_adapter_incompatible and never call
    router.apply().
    """
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
        ApplyResult,
    )
    from cauldron_content_operations.service import (
        ContentOperationService, _compute_canonical_changeset_hash,
    )
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import (
        _registry,
    )
    from cauldron_content_operations.models import ContentChangeRequest

    # Bypass register_adapter's contract check by inserting directly.
    class _Bad:
        supports_rollback = True
        reversible_adapter_version = 2
        def prepare(self, *a, **k): ...
        def record_applied(self, *a, **k): ...
        def record_rolled_back(self, *a, **k): ...
        def rollback(self, *a, **k): ...
        def has_rollback_artifact(self, *a, **k): ...
        def verify_applied_state(self, *a, **k): ...
        def verify_rolled_back_state(self, *a, **k): ...
        def inspect(self, *a, **k): ...
        # Deliberately no load_rollback_completion.
    _registry["flatfile"] = _Bad()
    try:
        user = _make_user(is_superuser=True, username="fz-inc")
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        cs_id = str(uuid.uuid4())
        op = ContentOperation(
            kind=ContentOperationKind.CREATE, provider="flatfile",
            collection="pages", item_id="p1", slug="p1", data={}, body="",
            schema="", status=ContentStatus.DRAFT, force=False,
        )
        cs = ContentChangeSet(id=cs_id, operations=(op,))
        payload_hash = _compute_canonical_changeset_hash(cs)
        router = MagicMock()
        router.resolve_provider.return_value = "flatfile"
        router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
        workspace = MagicMock()
        workspace.load_changeset.return_value = cs
        workspace.locks_dir = str(locks_dir)
        cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
        service = ContentOperationService(router=router, workspace=workspace, config=cfg)
        request_id = str(uuid.uuid4())
        ContentChangeRequest.objects.create(
            request_id=request_id, workspace_changeset_id=cs_id,
            provider_name="flatfile", lifecycle_state="approved",
            request_version=1, payload_hash=payload_hash, created_by=user,
        )
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_adapter_incompatible"
        router.apply.assert_not_called()
    finally:
        _registry.pop("flatfile", None)


def test_frozen_required_reversible_missing_adapter(tmp_path):
    """Item 1: provider in required_reversible_providers without an adapter
    → application.rollback_adapter_incompatible.
    """
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
        ApplyResult,
    )
    from cauldron_content_operations.service import (
        ContentOperationService, _compute_canonical_changeset_hash,
    )
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import reset_registry
    from cauldron_content_operations.models import ContentChangeRequest

    reset_registry()
    user = _make_user(is_superuser=True, username="fz-req")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    cs_id = str(uuid.uuid4())
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="flatfile",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    payload_hash = _compute_canonical_changeset_hash(cs)
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    workspace = MagicMock()
    workspace.load_changeset.return_value = cs
    workspace.locks_dir = str(locks_dir)
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(
        router=router, workspace=workspace, config=cfg,
        required_reversible_providers=frozenset({"flatfile"}),
    )
    request_id = str(uuid.uuid4())
    ContentChangeRequest.objects.create(
        request_id=request_id, workspace_changeset_id=cs_id,
        provider_name="flatfile", lifecycle_state="approved",
        request_version=1, payload_hash=payload_hash, created_by=user,
    )
    result = service.apply_change_request(request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "application.rollback_adapter_incompatible"
    router.apply.assert_not_called()


def test_frozen_non_required_provider_no_adapter_proceeds(tmp_path):
    """Item 1: provider NOT in required_reversible_providers without an
    adapter → apply proceeds nonreversible.
    """
    from cauldron_content.contracts import (
        ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus,
        ApplyResult,
    )
    from cauldron_content_operations.service import (
        ContentOperationService, _compute_canonical_changeset_hash,
    )
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import reset_registry
    from cauldron_content_operations.models import ContentChangeRequest

    reset_registry()
    user = _make_user(is_superuser=True, username="fz-nrq")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    cs_id = str(uuid.uuid4())
    op = ContentOperation(
        kind=ContentOperationKind.CREATE, provider="other",
        collection="pages", item_id="p1", slug="p1", data={}, body="",
        schema="", status=ContentStatus.DRAFT, force=False,
    )
    cs = ContentChangeSet(id=cs_id, operations=(op,))
    payload_hash = _compute_canonical_changeset_hash(cs)
    router = MagicMock()
    router.resolve_provider.return_value = "other"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    workspace = MagicMock()
    workspace.load_changeset.return_value = cs
    workspace.locks_dir = str(locks_dir)
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(
        router=router, workspace=workspace, config=cfg,
        required_reversible_providers=frozenset({"flatfile"}),
    )
    request_id = str(uuid.uuid4())
    ContentChangeRequest.objects.create(
        request_id=request_id, workspace_changeset_id=cs_id,
        provider_name="other", lifecycle_state="approved",
        request_version=1, payload_hash=payload_hash, created_by=user,
    )
    result = service.apply_change_request(request_id, user=user, expected_version=1)
    assert result.ok, result.error
    router.apply.assert_called_once()


def test_frozen_missing_load_rollback_completion_contract_violation():
    """Item 2: an adapter missing load_rollback_completion is rejected by
    validate_adapter_contract.
    """
    from cauldron_content_operations.reversible import validate_adapter_contract

    class _Missing:
        supports_rollback = True
        reversible_adapter_version = 2
        def prepare(self, *a, **k): ...
        def record_applied(self, *a, **k): ...
        def record_rolled_back(self, *a, **k): ...
        def rollback(self, *a, **k): ...
        def has_rollback_artifact(self, *a, **k): ...
        def verify_applied_state(self, *a, **k): ...
        def verify_rolled_back_state(self, *a, **k): ...
        def inspect(self, *a, **k): ...
        # No load_rollback_completion.
    violations = validate_adapter_contract(_Missing())
    assert any("load_rollback_completion" in v for v in violations), violations


def test_frozen_bad_record_applied_signature_rejected_at_registration():
    """Item 2: register_adapter refuses an adapter whose record_applied
    signature is incompatible with the required v2 shape.
    """
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, AdapterVersionMismatch,
    )

    class _BadSig:
        supports_rollback = True
        reversible_adapter_version = 2
        # record_applied without artifact_digest kwarg (bad shape).
        def prepare(self, cs_id, changeset): ...
        def record_applied(self, cs_id): ...
        def record_rolled_back(self, cs_id): ...
        def rollback(
            self, cs_id, *, force=False, is_superuser=False,
            expected_artifact_digest="", expected_entry_count=0,
        ): ...
        def has_rollback_artifact(self, cs_id): ...
        def verify_applied_state(
            self, cs_id, *, expected_artifact_digest="",
            expected_entry_count=0,
        ): ...
        def verify_rolled_back_state(
            self, cs_id, *, expected_artifact_digest="",
            expected_entry_count=0,
        ): ...
        def inspect(self, cs_id): ...
        def load_rollback_completion(self, cs_id): ...
    unregister_adapter("flatfile")
    with pytest.raises(AdapterVersionMismatch):
        register_adapter("flatfile", _BadSig())


def test_frozen_prepare_returning_dict_rejected(tmp_path):
    """Item 2: application MUST reject prepare() returning a plain dict."""
    service, user, request_id, adapter, router, unregister = _prep_test_setup(
        tmp_path, {"artifact_digest": "a" * 64, "entry_count": 1},
    )
    try:
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()
    finally:
        unregister("flatfile")


def test_frozen_preparation_result_63_char_digest_rejected(tmp_path):
    """Item 2: PreparationResult with an invalid digest is rejected."""
    from cauldron_content_operations.reversible import PreparationResult
    service, user, request_id, adapter, router, unregister = _prep_test_setup(
        tmp_path,
        PreparationResult(artifact_digest="a" * 63, entry_count=1),
    )
    try:
        result = service.apply_change_request(request_id, user=user, expected_version=1)
        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()
    finally:
        unregister("flatfile")


# ---------------------------------------------------------------------------
# Frozen contract pass: evidence parser regression tests
# ---------------------------------------------------------------------------


def test_frozen_evidence_parser_missing_version():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            {"cs_id": "x", "files": []},
            cs_id="x", trusted_digest="a" * 64, trusted_entry_count=1,
        )


def test_frozen_evidence_parser_version_1_rejected():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            {"version": 1, "cs_id": "x", "files": []},
            cs_id="x", trusted_digest="a" * 64, trusted_entry_count=1,
        )


def test_frozen_evidence_parser_missing_cs_id():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            {"version": 2, "files": []},
            cs_id="x", trusted_digest="a" * 64, trusted_entry_count=1,
        )


def test_frozen_evidence_parser_empty_trusted_digest():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            {"version": 2, "cs_id": "x", "files": []},
            cs_id="x", trusted_digest="", trusted_entry_count=1,
        )


def test_frozen_evidence_parser_string_existed_rejected():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    art = {
        "version": 2, "cs_id": "x", "files": [{
            "op_index": 0, "collection": "pages", "item_id": "p1",
            "kind": "create", "rel_path": "pages/p1.md",
            "snap_name": "snap_0.bin", "existed": "true",
            "pre_hash": "", "snap_sha256": "",
        }],
    }
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            art, cs_id="x", trusted_digest="a" * 64, trusted_entry_count=1,
        )


def test_frozen_evidence_parser_missing_snap_sha256_when_existed():
    from cauldron_workspace_flatfile.reversible import (
        parse_rollback_artifact, EvidenceValidationError,
    )
    art = {
        "version": 2, "cs_id": "x", "files": [{
            "op_index": 0, "collection": "pages", "item_id": "p1",
            "kind": "update", "rel_path": "pages/p1.md",
            "snap_name": "snap_0.bin", "existed": True,
            "pre_hash": "a" * 64, "snap_sha256": "",
        }],
    }
    with pytest.raises(EvidenceValidationError):
        parse_rollback_artifact(
            art, cs_id="x", trusted_digest="a" * 64, trusted_entry_count=1,
        )


def test_frozen_post_state_missing_artifact_digest():
    from cauldron_workspace_flatfile.reversible import (
        parse_post_state, EvidenceValidationError, RollbackEntry,
    )
    entries = [RollbackEntry(
        op_index=0, collection="pages", item_id="p1", kind="create",
        rel_path="pages/p1.md", snap_name="snap_0.bin", existed=False,
        pre_hash="", snap_sha256="",
    )]
    with pytest.raises(EvidenceValidationError):
        parse_post_state(
            {"version": 2, "cs_id": "x", "records": []},
            cs_id="x", trusted_digest="a" * 64, rollback_entries=entries,
        )


def test_frozen_post_state_string_expected_present_rejected():
    from cauldron_workspace_flatfile.reversible import (
        parse_post_state, EvidenceValidationError, RollbackEntry,
    )
    entries = [RollbackEntry(
        op_index=0, collection="pages", item_id="p1", kind="create",
        rel_path="pages/p1.md", snap_name="snap_0.bin", existed=False,
        pre_hash="", snap_sha256="",
    )]
    doc = {
        "version": 2, "cs_id": "x", "artifact_digest": "a" * 64,
        "records": [{
            "op_index": 0, "collection": "pages", "item_id": "p1",
            "kind": "create", "rel_path": "pages/p1.md",
            "expected_present": "true", "sha256": "a" * 64,
        }],
    }
    with pytest.raises(EvidenceValidationError):
        parse_post_state(doc, cs_id="x", trusted_digest="a" * 64, rollback_entries=entries)


def test_frozen_post_state_collection_mismatch():
    from cauldron_workspace_flatfile.reversible import (
        parse_post_state, EvidenceValidationError, RollbackEntry,
    )
    entries = [RollbackEntry(
        op_index=0, collection="pages", item_id="p1", kind="create",
        rel_path="pages/p1.md", snap_name="snap_0.bin", existed=False,
        pre_hash="", snap_sha256="",
    )]
    doc = {
        "version": 2, "cs_id": "x", "artifact_digest": "a" * 64,
        "records": [{
            "op_index": 0, "collection": "posts", "item_id": "p1",
            "kind": "create", "rel_path": "pages/p1.md",
            "expected_present": True, "sha256": "a" * 64,
        }],
    }
    with pytest.raises(EvidenceValidationError):
        parse_post_state(doc, cs_id="x", trusted_digest="a" * 64, rollback_entries=entries)


def test_frozen_post_state_item_id_mismatch():
    from cauldron_workspace_flatfile.reversible import (
        parse_post_state, EvidenceValidationError, RollbackEntry,
    )
    entries = [RollbackEntry(
        op_index=0, collection="pages", item_id="p1", kind="create",
        rel_path="pages/p1.md", snap_name="snap_0.bin", existed=False,
        pre_hash="", snap_sha256="",
    )]
    doc = {
        "version": 2, "cs_id": "x", "artifact_digest": "a" * 64,
        "records": [{
            "op_index": 0, "collection": "pages", "item_id": "different",
            "kind": "create", "rel_path": "pages/p1.md",
            "expected_present": True, "sha256": "a" * 64,
        }],
    }
    with pytest.raises(EvidenceValidationError):
        parse_post_state(doc, cs_id="x", trusted_digest="a" * 64, rollback_entries=entries)


# ---------------------------------------------------------------------------
# Frozen contract pass: routing-aware system checks
# ---------------------------------------------------------------------------


def test_frozen_flatfile_routing_without_cms_config_fires_e020():
    from django.test import override_settings
    from cauldron_admin_content.checks import (
        check_admin_content_flatfile_routing,
    )
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.admin.content": {},
            "cauldron.content": {
                "routing": {"default_provider": "flatfile", "collections": {}},
            },
            "cauldron.workspace.flatfile": {"workspace_root": "/tmp/ws-x"},
        }
    ):
        errors = check_admin_content_flatfile_routing(None)
    ids = [e.id for e in errors]
    assert "content_admin.E020" in ids


def test_frozen_non_flatfile_routing_no_flatfile_errors():
    from django.test import override_settings
    from cauldron_admin_content.checks import (
        check_admin_content_flatfile_routing,
    )
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.admin.content": {},
            "cauldron.content": {
                "routing": {"default_provider": "other", "collections": {}},
            },
            "cauldron.workspace.flatfile": {"workspace_root": "/tmp/ws-y"},
        }
    ):
        errors = check_admin_content_flatfile_routing(None)
    ids = [e.id for e in errors]
    for eid in ("content_admin.E020", "content_admin.E021",
                "content_admin.E022", "content_admin.E023"):
        assert eid not in ids
