"""Tests for ContentOperationService."""
import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.django_db


def _make_user(perms=None, is_superuser=False, username="testuser"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="password")
    if is_superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    if perms:
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        for perm_codename in perms:
            try:
                perm = Permission.objects.get(codename=perm_codename)
            except Permission.DoesNotExist:
                continue
            user.user_permissions.add(perm)
        user.refresh_from_db()
    return user


def _make_service(workspace_root=None):
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    # Build a mock router
    router = MagicMock()
    router.list_items.return_value = []
    router.get_by_id.return_value = None
    router.resolve_provider.return_value = "flatfile"

    # Build a workspace mock that echoes the created changeset back on load,
    # so that integrity checks on approve/apply/preview find the same payload
    # that was originally created.
    workspace = MagicMock()
    _saved: dict = {}

    def _ws_create(cs):
        _saved[cs.id] = cs

    def _ws_load(cs_id):
        return _saved.get(cs_id)

    workspace.create.side_effect = _ws_create
    workspace.load_changeset.side_effect = _ws_load
    workspace.save_result.return_value = None
    workspace.save_application_result.return_value = None
    workspace.save_rollback_result.return_value = None
    workspace.load_application_result.return_value = None
    workspace.load_rollback_result.return_value = None

    # Return success by default
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())

    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    return ContentOperationService(router=router, workspace=workspace, snapshots=None, config=cfg)


def test_anonymous_user_denied():
    from cauldron_content_operations.service import PermissionDenied
    service = _make_service()
    with pytest.raises(PermissionDenied):
        service.list_items("pages", user=None)


def test_user_without_perms_denied():
    from cauldron_content_operations.service import PermissionDenied
    user = _make_user()
    service = _make_service()
    with pytest.raises(PermissionDenied):
        service.list_items("pages", user=user)


def test_superuser_can_list_items():
    user = _make_user(is_superuser=True)
    service = _make_service()
    items = service.list_items("pages", user=user)
    assert items == []


def test_create_change_request_requires_perm():
    from cauldron_content_operations.service import PermissionDenied
    user = _make_user()
    service = _make_service()
    with pytest.raises(PermissionDenied):
        service.create_change_request(
            user=user,
            operations=[{"kind": "create", "collection": "pages", "item_id": "p1"}],
            provider_name="flatfile",
        )


def test_create_change_request_works():
    user = _make_user(is_superuser=True)
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert result.ok
    assert result.request_id
    assert result.lifecycle_state == "proposed"


def test_max_operations_enforced():
    user = _make_user(is_superuser=True)
    service = _make_service()
    ops = [{"kind": "create", "collection": "pages", "item_id": f"p{i}", "slug": f"p{i}", "data": {}} for i in range(11)]
    result = service.create_change_request(user=user, operations=ops, provider_name="flatfile")
    assert not result.ok
    assert result.error.code == "operations.too_many"


def test_idempotency_key_prevents_duplicate():
    """Same key + same payload returns the original request as idempotent."""
    user = _make_user(is_superuser=True)
    service = _make_service()
    ops = [{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}]
    r1 = service.create_change_request(
        user=user,
        operations=ops,
        provider_name="flatfile",
        idempotency_key="my-key-1",
    )
    assert r1.ok
    r2 = service.create_change_request(
        user=user,
        operations=ops,
        provider_name="flatfile",
        idempotency_key="my-key-1",
    )
    assert r2.ok
    assert r2.meta.get("idempotent")
    assert r1.request_id == r2.request_id


def test_self_approval_denied():
    user = _make_user(is_superuser=True)
    service = _make_service()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    # Move to validated state directly to allow approval attempt
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=r.request_id)
    cr.lifecycle_state = "validated"
    cr.save()
    result = service.approve_change_request(
        r.request_id, user=user, expected_version=cr.request_version
    )
    assert not result.ok
    assert "self_approval" in result.error.code


def test_get_change_request():
    user = _make_user(is_superuser=True)
    service = _make_service()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    detail = service.get_change_request(r.request_id, user=user)
    assert detail is not None
    assert detail.request_id == r.request_id
    assert detail.lifecycle_state == "proposed"


def test_module_manifest():
    from cauldron_content_operations.module import module
    assert module.slug == "cauldron.content.operations"
    assert "content.operations" in module.manifest.provides
    assert "content.authorization" in module.manifest.provides
    assert "content.audit" in module.manifest.provides


def test_permissions_exist():
    """Required permissions are created via migration."""
    from django.contrib.auth.models import Permission
    codenames = [
        "view_published_content", "view_draft_content", "view_content_change_requests",
        "propose_content_changes",
        "validate_content_changes", "approve_content_changes", "reject_content_changes",
        "apply_content_changes", "rollback_content_changes", "view_content_audit",
    ]
    for codename in codenames:
        assert Permission.objects.filter(codename=codename).exists(), f"Missing permission: {codename}"


# ---------------------------------------------------------------------------
# Hardening regression tests
# ---------------------------------------------------------------------------


def test_required_expected_version_for_validate():
    """validate_change_request rejects expected_version=0 with conflict.version_required."""
    user = _make_user(is_superuser=True, username="verzero")
    service = _make_service()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=0)
    assert not result.ok
    assert result.error.code == "conflict.version_required"


def test_required_expected_version_for_approve():
    user = _make_user(is_superuser=True, username="verzero2")
    service = _make_service()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    result = service.approve_change_request(r.request_id, user=user, expected_version=0)
    assert not result.ok
    assert result.error.code == "conflict.version_required"


def test_source_ref_not_in_public_dict():
    """ContentItemResult.to_dict() must not expose source_ref."""
    from cauldron_content_operations.results import ContentItemResult
    item = ContentItemResult(
        id="i1", collection="pages", slug="i1", status="published",
        schema="", data={}, body="", hash="abc", provider="flatfile",
        source_ref="/home/user/site/content/pages/i1.md",
    )
    d = item.to_dict()
    assert "source_ref" not in d
    assert "/home/" not in str(d)


def test_idempotency_payload_mismatch():
    """Same key + different payload returns idempotency.payload_mismatch."""
    user = _make_user(is_superuser=True, username="idem2")
    service = _make_service()
    r1 = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
        idempotency_key="key-mismatch",
    )
    assert r1.ok
    r2 = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "DIFFERENT", "slug": "DIFFERENT", "data": {}}],
        provider_name="flatfile",
        idempotency_key="key-mismatch",
    )
    assert not r2.ok
    assert r2.error.code == "idempotency.payload_mismatch"


def test_mixed_provider_proposal_rejected():
    """Proposals targeting multiple providers must be rejected."""
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    user = _make_user(is_superuser=True, username="mixeduser")
    router = MagicMock()
    router.list_items.return_value = []

    def _resolve(coll):
        return "flatfile" if coll == "pages" else "sql"

    router.resolve_provider.side_effect = _resolve
    workspace = MagicMock()
    workspace.create.return_value = None
    cfg = ContentOperationsConfig(
        require_approval=True,
        allow_self_approval=False,
        max_operations_per_change_set=10,
    )
    service = ContentOperationService(
        router=router, workspace=workspace, snapshots=None, config=cfg
    )
    ops = [
        {"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}},
        {"kind": "create", "collection": "articles", "item_id": "a1", "slug": "a1", "data": {}},
    ]
    result = service.create_change_request(
        user=user, operations=ops, provider_name="flatfile"
    )
    assert not result.ok
    assert result.error.code == "operations.mixed_providers_not_supported"


def test_strict_bool_config_rejects_string():
    """Config must not accept string 'false' as a bool."""
    from cauldron_content_operations.config import _strict_bool, _strict_positive_int
    with pytest.raises(TypeError):
        _strict_bool("false", "require_approval", True)
    with pytest.raises(TypeError):
        _strict_bool(0, "require_approval", True)
    # Also reject strings for positive int
    with pytest.raises(TypeError):
        _strict_positive_int("10", "max_operations_per_change_set", 100)
    with pytest.raises(TypeError):
        _strict_positive_int(True, "max_operations_per_change_set", 100)


def test_view_content_change_requests_permission_required():
    """list_change_requests now requires view_content_change_requests."""
    from cauldron_content_operations.service import PermissionDenied

    user = _make_user(perms=["view_published_content"], username="listuser")
    service = _make_service()
    with pytest.raises(PermissionDenied) as exc_info:
        service.list_change_requests(user=user)
    assert exc_info.value.code == "auth.permission_denied"


def test_view_published_content_grants_list_items():
    """view_published_content still allows list_items."""
    user = _make_user(perms=["view_published_content"], username="viewuser")
    service = _make_service()
    items = service.list_items("pages", user=user)
    assert items == []


def test_get_preview_requires_change_request_permission():
    """get_preview requires view_content_change_requests."""
    from cauldron_content_operations.service import PermissionDenied

    user = _make_user(perms=["view_published_content"], username="previewuser")
    service = _make_service()
    with pytest.raises(PermissionDenied):
        service.get_preview("some-id", user=user)


def test_empty_operations_rejected():
    """create_change_request rejects empty operations lists."""
    user = _make_user(is_superuser=True, username="emptyuser")
    service = _make_service()
    result = service.create_change_request(
        user=user, operations=[], provider_name="flatfile"
    )
    assert not result.ok
    assert result.error.code == "operations.empty"


def test_audit_actor_none_when_user_pk_none():
    """append_audit_event stores actor=None when user has no pk."""
    from cauldron_content_operations.audit import append_audit_event
    from cauldron_content_operations.models import ContentChangeRequest

    class UnsavedUser:
        pk = None
        is_authenticated = True
        is_active = True

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-audit",
        provider_name="flatfile",
    )
    event = append_audit_event(
        change_request=cr,
        event_type="test.actor_none",
        actor=UnsavedUser(),
        resulting_state="proposed",
    )
    assert event.actor is None


# ---------------------------------------------------------------------------
# Fix 1: Status validation
# ---------------------------------------------------------------------------


def test_invalid_status_rejected():
    user = _make_user(is_superuser=True, username="invst1")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "status": "INVALID_STATUS"}],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.invalid_status"


def test_invalid_status_no_workspace_artifact():
    """workspace.create is never called when status is invalid."""
    from unittest.mock import MagicMock
    from cauldron_content.contracts import ApplyResult
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    user = _make_user(is_superuser=True, username="invst2")
    ws = MagicMock()
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)
    service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "status": "BADSTATUS"}],
        provider_name="flatfile",
    )
    ws.create.assert_not_called()


def test_invalid_status_no_db_record():
    from cauldron_content_operations.models import ContentChangeRequest
    user = _make_user(is_superuser=True, username="invst3")
    service = _make_service()
    count_before = ContentChangeRequest.objects.count()
    service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "status": "NOTVALID"}],
        provider_name="flatfile",
    )
    assert ContentChangeRequest.objects.count() == count_before


def test_valid_draft_status():
    user = _make_user(is_superuser=True, username="draftstatus")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "status": "draft"}],
        provider_name="flatfile",
    )
    assert result.ok


def test_valid_published_status():
    user = _make_user(is_superuser=True, username="pubstatus")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "status": "published"}],
        provider_name="flatfile",
    )
    assert result.ok


def test_missing_status_uses_default_draft():
    user = _make_user(is_superuser=True, username="defstatus")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert result.ok


# ---------------------------------------------------------------------------
# Fix 2: Force field rejection
# ---------------------------------------------------------------------------


def test_force_field_rejected_in_proposal():
    user = _make_user(is_superuser=True, username="forceuser")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "force": True}],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.force_not_allowed"


def test_force_false_also_rejected():
    """Even force=False is forbidden; presence of the key is what matters."""
    user = _make_user(is_superuser=True, username="forcefuser")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "force": False}],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.force_not_allowed"


# ---------------------------------------------------------------------------
# Fix 3: Repository validation in validate_change_request
# ---------------------------------------------------------------------------


def _make_service_with_repo(validate_return=None, get_by_id_return=None):
    from unittest.mock import MagicMock
    from cauldron_content.contracts import ApplyResult, ValidationResult
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    mock_repo = MagicMock()
    mock_repo.validate.return_value = validate_return or ValidationResult.ok()

    router = MagicMock()
    router.list_items.return_value = []
    router.get_by_id.return_value = get_by_id_return
    router.resolve_provider.return_value = "flatfile"
    router.get_repo.return_value = mock_repo
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())

    saved: dict = {}

    def _ws_create(cs):
        saved[cs.id] = cs

    def _ws_load(cs_id):
        if cs_id not in saved:
            raise KeyError(f"Changeset {cs_id!r} not found")
        return saved[cs_id]

    workspace = MagicMock()
    workspace.create.side_effect = _ws_create
    workspace.load_changeset.side_effect = _ws_load
    workspace.save_application_result.return_value = None

    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    return ContentOperationService(router=router, workspace=workspace, config=cfg), mock_repo


def test_validation_calls_repository_validate():
    """validate_change_request calls repo.validate() for CREATE operations."""
    user = _make_user(is_superuser=True, username="valrep1")
    service, mock_repo = _make_service_with_repo()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert result.ok
    mock_repo.validate.assert_called_once()


def test_validation_repo_issues_fail_validation():
    """repo.validate() returning issues causes validation to fail."""
    from cauldron_content.contracts import ValidationResult, ValidationIssue

    user = _make_user(is_superuser=True, username="valrep2")
    bad_vr = ValidationResult.failed([
        ValidationIssue(code="schema.missing_field", message="Missing 'title'", collection="pages", item_id="p1")
    ])
    service, _ = _make_service_with_repo(validate_return=bad_vr)
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "validation.failed"


def test_validation_update_requires_expected_hash():
    """UPDATE without expected_hash fails validation."""
    user = _make_user(is_superuser=True, username="valupd1")
    service, _ = _make_service_with_repo()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "update", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {"title": "X"}}],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "validation.failed"
    assert "update_requires_expected_hash" in str(result.error.details)


def test_validation_delete_requires_expected_hash():
    """DELETE without expected_hash fails validation."""
    user = _make_user(is_superuser=True, username="valdel1")
    service, _ = _make_service_with_repo()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "delete", "collection": "pages", "item_id": "p1"}],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "validation.failed"
    assert "delete_requires_expected_hash" in str(result.error.details)


def test_validation_stale_hash_blocked():
    """UPDATE with stale expected_hash is blocked."""
    from cauldron_content.contracts import ContentItem, ContentStatus

    current_item = ContentItem(
        id="p1", collection="pages", slug="p1",
        status=ContentStatus.PUBLISHED, schema="", data={},
        body="original", hash="actual_hash_abc", provider="flatfile",
    )
    user = _make_user(is_superuser=True, username="valstale")
    service, _ = _make_service_with_repo(get_by_id_return=current_item)
    r = service.create_change_request(
        user=user,
        operations=[{
            "kind": "update", "collection": "pages", "item_id": "p1", "slug": "p1",
            "data": {"title": "Updated"}, "expected_hash": "stale_hash_xyz",
        }],
        provider_name="flatfile",
    )
    assert r.ok
    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "validation.failed"
    assert "stale_hash" in str(result.error.details)


# ---------------------------------------------------------------------------
# Fix 5+6: Apply sequence and artifact failure handling
# ---------------------------------------------------------------------------


def test_adapter_prepare_failure_prevents_mutation(tmp_path):
    """prepare() failure → apply_failed; router.apply is never called."""
    from unittest.mock import MagicMock
    from cauldron_content.contracts import ApplyResult, ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus
    from cauldron_content_operations.service import ContentOperationService, _compute_canonical_changeset_hash
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import register_adapter, unregister_adapter
    from cauldron_content_operations.models import ContentChangeRequest
    import uuid

    user = _make_user(is_superuser=True, username="prepfail")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.supports_rollback = True
    mock_adapter.prepare.side_effect = RuntimeError("disk full")

    register_adapter("flatfile", mock_adapter)
    try:
        cs_id = str(uuid.uuid4())
        op = ContentOperation(kind=ContentOperationKind.CREATE, provider="flatfile", collection="pages", item_id="p1", slug="p1", data={}, body="", schema="", status=ContentStatus.DRAFT, force=False)
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

        result = service.apply_change_request(request_id, user=user, expected_version=1)

        assert not result.ok
        assert result.error.code == "application.rollback_artifact_failed"
        router.apply.assert_not_called()

        cr = ContentChangeRequest.objects.get(request_id=request_id)
        assert cr.lifecycle_state == "apply_failed"
    finally:
        unregister_adapter("flatfile")


def test_result_persistence_failure_enters_reconciliation_required(tmp_path):
    """record_applied() failure after successful mutation → reconciliation_required."""
    from unittest.mock import MagicMock
    from cauldron_content.contracts import ApplyResult, ContentChangeSet, ContentOperation, ContentOperationKind, ContentStatus
    from cauldron_content_operations.service import ContentOperationService, _compute_canonical_changeset_hash
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.reversible import register_adapter, unregister_adapter
    from cauldron_content_operations.models import ContentChangeRequest
    import uuid

    user = _make_user(is_superuser=True, username="reconreq")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.supports_rollback = True
    mock_adapter.prepare.return_value = None
    mock_adapter.record_applied.side_effect = OSError("storage error")

    register_adapter("flatfile", mock_adapter)
    try:
        cs_id = str(uuid.uuid4())
        op = ContentOperation(kind=ContentOperationKind.CREATE, provider="flatfile", collection="pages", item_id="p1", slug="p1", data={}, body="", schema="", status=ContentStatus.DRAFT, force=False)
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

        result = service.apply_change_request(request_id, user=user, expected_version=1)

        assert not result.ok
        assert result.lifecycle_state == "reconciliation_required"

        cr = ContentChangeRequest.objects.get(request_id=request_id)
        assert cr.lifecycle_state == "reconciliation_required"
        assert cr.last_error_code == "application.reconciliation_required"
    finally:
        unregister_adapter("flatfile")


# ---------------------------------------------------------------------------
# Item 2: Authoritative provider identity
# ---------------------------------------------------------------------------


def test_item2_empty_provider_accepted_and_stored_from_routing():
    """Empty provider_name is accepted; routed provider is stored."""
    user = _make_user(is_superuser=True, username="item2empty")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert result.ok
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.get(request_id=result.request_id)
    assert cr.provider_name == "flatfile"


def test_item2_matching_provider_accepted():
    user = _make_user(is_superuser=True, username="item2match")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert result.ok


def test_item2_mismatched_provider_rejected():
    user = _make_user(is_superuser=True, username="item2mismatch")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="different-provider",
    )
    assert not result.ok
    assert result.error.code == "operations.provider_mismatch"


def test_item2_unroutable_collection_rejected():
    from unittest.mock import MagicMock
    from cauldron_content.contracts import ApplyResult
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    user = _make_user(is_superuser=True, username="item2unroutable")
    router = MagicMock()

    def _resolve(coll):
        raise RuntimeError(f"no route for {coll!r}")

    router.resolve_provider.side_effect = _resolve
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    workspace = MagicMock()
    workspace.create.return_value = None
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)
    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "unknown", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert not result.ok
    assert result.error.code == "operations.unroutable_collection"


def test_item2_op_data_not_dict_rejected():
    user = _make_user(is_superuser=True, username="item2notdict")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=["not-a-dict"],  # type: ignore[list-item]
        provider_name="",
    )
    assert not result.ok
    assert result.error.code == "operations.invalid_operation"


# ---------------------------------------------------------------------------
# Item 3: Workspace required for proposals
# ---------------------------------------------------------------------------


def test_item3_no_workspace_rejects_proposal():
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent

    user = _make_user(is_superuser=True, username="item3none")
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=None, config=cfg)

    cr_before = ContentChangeRequest.objects.count()
    ae_before = ContentAuditEvent.objects.count()

    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "workspace.unavailable"
    assert ContentChangeRequest.objects.count() == cr_before
    assert ContentAuditEvent.objects.count() == ae_before


# ---------------------------------------------------------------------------
# Item 1: Canonical hash + workspace integrity check
# ---------------------------------------------------------------------------


def test_item1_payload_integrity_mismatch_blocks_apply(tmp_path):
    """Tampering the workspace payload after creation blocks apply."""
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item1tamper")
    ws_root = tmp_path / "ws"
    cfg_ws = WorkspaceConfig(workspace_root=ws_root)
    workspace = ChangeSetStore(cfg_ws)
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {"title": "orig"}}],
        provider_name="",
    )
    assert result.ok
    cr = ContentChangeRequest.objects.get(request_id=result.request_id)

    # Tamper the persisted payload.
    import json
    cs_dir = ws_root / "change-sets" / cr.workspace_changeset_id
    payload_path = cs_dir / "payload.json"
    data = json.loads(payload_path.read_text())
    data["operations"][0]["data"] = {"title": "TAMPERED"}
    payload_path.write_text(json.dumps(data, indent=2, sort_keys=True))

    # Now attempt to apply — must be blocked.
    result = service.apply_change_request(result.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "workspace.payload_integrity_mismatch"


def test_item1_force_persisted_blocks_apply(tmp_path):
    """Manual tampering that sets force=True in payload.json is refused."""
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item1force")
    ws_root = tmp_path / "ws"
    cfg_ws = WorkspaceConfig(workspace_root=ws_root)
    workspace = ChangeSetStore(cfg_ws)
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    result = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert result.ok
    cr = ContentChangeRequest.objects.get(request_id=result.request_id)

    # Tamper with force=True.
    import json
    cs_dir = ws_root / "change-sets" / cr.workspace_changeset_id
    payload_path = cs_dir / "payload.json"
    data = json.loads(payload_path.read_text())
    data["operations"][0]["force"] = True
    payload_path.write_text(json.dumps(data, indent=2, sort_keys=True))

    result = service.apply_change_request(result.request_id, user=user, expected_version=1)
    assert not result.ok
    # Either force_not_allowed (detected first) or payload_integrity_mismatch (also acceptable)
    assert result.error.code in (
        "workspace.force_not_allowed",
        "workspace.payload_integrity_mismatch",
    )


# ---------------------------------------------------------------------------
# Item 6: Force rollback requires superuser (service-level enforcement)
# ---------------------------------------------------------------------------


def test_item6_force_rollback_requires_superuser():
    """Non-superuser cannot force rollback even at service level."""
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    user = _make_user(perms=["rollback_content_changes"], username="item6nonsu")
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    ws = MagicMock()
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    result = service.rollback_change_request(
        "some-id", user=user, force=True, expected_version=1,
    )
    assert not result.ok
    assert result.error.code == "rollback.force_requires_superuser"


# ---------------------------------------------------------------------------
# Item 12: Audit sequence retry
# ---------------------------------------------------------------------------


def test_item12_audit_retry_on_integrity_error():
    """append_audit_event retries on IntegrityError and eventually succeeds."""
    from unittest.mock import patch
    from django.db import IntegrityError
    from cauldron_content_operations.audit import append_audit_event
    from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-retry",
        provider_name="flatfile",
    )
    # First save raises IntegrityError; second succeeds.
    original_save = ContentAuditEvent.save
    call_count = {"n": 0}

    def flaky_save(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IntegrityError("simulated race")
        return original_save(self, *args, **kwargs)

    with patch.object(ContentAuditEvent, "save", flaky_save):
        event = append_audit_event(
            change_request=cr,
            event_type="test.retry",
            resulting_state="proposed",
        )
    assert event.pk is not None
    assert call_count["n"] >= 2


def test_item12_audit_gives_up_after_max_retries():
    """After max retries, AuditSequenceError is raised."""
    from unittest.mock import patch
    from django.db import IntegrityError
    from cauldron_content_operations.audit import (
        append_audit_event, AuditSequenceError,
    )
    from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-noretry",
        provider_name="flatfile",
    )

    def always_fail(self, *args, **kwargs):
        raise IntegrityError("persistent race")

    with patch.object(ContentAuditEvent, "save", always_fail):
        with pytest.raises(AuditSequenceError):
            append_audit_event(
                change_request=cr,
                event_type="test.always_fail",
                resulting_state="proposed",
            )


# ---------------------------------------------------------------------------
# Item 13: Idempotency creation race handling
# ---------------------------------------------------------------------------


def test_item13_concurrent_create_returns_winner(tmp_path):
    """Simulated IntegrityError from concurrent insert returns the winner as idempotent."""
    from unittest.mock import patch
    from django.db import IntegrityError
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item13race")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    # First create wins.
    r1 = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
        idempotency_key="race-key",
    )
    assert r1.ok

    # Force IntegrityError on the next create; hitting the same idempotency key,
    # the service should re-query and return the winner as idempotent.
    original_create = ContentChangeRequest.objects.create
    ic = {"n": 0}

    def flaky_create(*args, **kwargs):
        ic["n"] += 1
        # Also short-circuit the idempotency lookup by using a fresh key that
        # collides only at insert time. We simulate the race by simply raising.
        raise IntegrityError("concurrent insert")

    with patch.object(ContentChangeRequest.objects, "create", flaky_create):
        r2 = service.create_change_request(
            user=user,
            operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
            provider_name="",
            idempotency_key="race-key",
        )
    # We should recover as idempotent and return the winner (r1).
    assert r2.ok
    assert r2.meta.get("idempotent")
    assert r2.request_id == r1.request_id


# ---------------------------------------------------------------------------
# Item 14: Workspace state synchronization
# ---------------------------------------------------------------------------


def test_item14_validate_transitions_workspace_state(tmp_path):
    """After successful validate_change_request, workspace manifest reflects validated."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore, ChangeSetState
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item14validate")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    router.get_by_id.return_value = None
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert r.ok

    result = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert result.ok
    cs_id = _get_ws_cs_id(r.request_id)
    assert ws.get_state(cs_id) == ChangeSetState.VALIDATED


def _get_ws_cs_id(request_id):
    from cauldron_content_operations.models import ContentChangeRequest
    return ContentChangeRequest.objects.get(request_id=request_id).workspace_changeset_id


# ---------------------------------------------------------------------------
# Item 8: Reconciliation uses provider verify_applied_state / verify_rolled_back_state
# ---------------------------------------------------------------------------


def test_item8_reconcile_applying_finalizes_with_verified(tmp_path):
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item8applyok")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        request_id="rid-item8-1",
        workspace_changeset_id="cs-item8-1",
        provider_name="flatfile",
        lifecycle_state="applying",
    )
    ws.create.__self__ if False else None  # keep type checker quiet
    ws.save_application_result("cs-item8-1", {"applied_count": 1, "correlation_id": "c1"})

    adapter = MagicMock()
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-item8-1"]
    assert matched
    assert matched[0]["action"] == "finalize_applied"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "applied"


def test_item8_reconcile_applying_leaves_when_verify_fails(tmp_path):
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item8applyfail")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        request_id="rid-item8-2",
        workspace_changeset_id="cs-item8-2",
        provider_name="flatfile",
        lifecycle_state="applying",
    )
    ws.save_application_result("cs-item8-2", {"applied_count": 1, "correlation_id": "c2"})

    adapter = MagicMock()
    adapter.verify_applied_state.return_value = VerificationResult(
        status="mismatch", reason="drifted",
    )
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-item8-2"]
    assert matched
    assert matched[0]["action"] == "leave_ambiguous"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "reconciliation_required"


def test_item8_reconcile_rolling_back_never_finalizes_as_applied(tmp_path):
    """Guarantee that a rolling_back state never gets finalized as applied."""
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.reversible import (
        register_adapter, unregister_adapter, VerificationResult,
    )
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item8rb")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        request_id="rid-item8-3",
        workspace_changeset_id="cs-item8-3",
        provider_name="flatfile",
        lifecycle_state="rolling_back",
    )
    # Only an application_result exists but state is rolling_back — must NOT finalize as applied.
    ws.save_application_result("cs-item8-3", {"applied_count": 1, "correlation_id": "c3"})

    adapter = MagicMock()
    adapter.verify_applied_state.return_value = VerificationResult(status="verified")
    adapter.verify_rolled_back_state.return_value = VerificationResult(status="missing_evidence")
    register_adapter("flatfile", adapter)
    try:
        results = service.reconcile(user=user, dry_run=False)
    finally:
        unregister_adapter("flatfile")

    matched = [r for r in results if r["request_id"] == "rid-item8-3"]
    assert matched
    cr.refresh_from_db()
    assert cr.lifecycle_state != "applied"
    assert cr.lifecycle_state == "reconciliation_required"


# ---------------------------------------------------------------------------
# Item 1: payload integrity during approval
# ---------------------------------------------------------------------------


def test_item1_approval_blocks_on_payload_tampering(tmp_path):
    """Tampering payload.json between validation and approval blocks approve."""
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from unittest.mock import MagicMock

    proposer = _make_user(is_superuser=True, username="item1apA")
    ws_root = tmp_path / "ws"
    workspace = ChangeSetStore(WorkspaceConfig(workspace_root=ws_root))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    r = service.create_change_request(
        user=proposer,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {"t": "a"}}],
        provider_name="",
    )
    assert r.ok
    # Advance through validation.
    v = service.validate_change_request(r.request_id, user=proposer, expected_version=1)
    assert v.ok
    # Tamper payload.json between validate and approve.
    cr = ContentChangeRequest.objects.get(request_id=r.request_id)
    import json
    payload = json.loads((ws_root / "change-sets" / cr.workspace_changeset_id / "payload.json").read_text())
    payload["operations"][0]["data"] = {"t": "TAMPERED"}
    (ws_root / "change-sets" / cr.workspace_changeset_id / "payload.json").write_text(json.dumps(payload))
    ap = service.approve_change_request(r.request_id, user=proposer, expected_version=v.request_version)
    assert not ap.ok
    assert ap.error.code == "workspace.payload_integrity_mismatch"
    # State was not mutated.
    cr.refresh_from_db()
    assert cr.lifecycle_state == "validated"


def test_item1_approval_blocks_on_force_persisted(tmp_path):
    """force=True persisted in payload blocks approve."""
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item1apF")
    ws_root = tmp_path / "ws"
    workspace = ChangeSetStore(WorkspaceConfig(workspace_root=ws_root))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert r.ok
    v = service.validate_change_request(r.request_id, user=user, expected_version=1)
    assert v.ok
    cr = ContentChangeRequest.objects.get(request_id=r.request_id)
    import json
    payload_path = ws_root / "change-sets" / cr.workspace_changeset_id / "payload.json"
    payload = json.loads(payload_path.read_text())
    payload["operations"][0]["force"] = True
    payload_path.write_text(json.dumps(payload))
    ap = service.approve_change_request(r.request_id, user=user, expected_version=v.request_version)
    assert not ap.ok
    assert ap.error.code in (
        "workspace.force_not_allowed",
        "workspace.payload_integrity_mismatch",
    )


# ---------------------------------------------------------------------------
# Item 2: provider routing drift
# ---------------------------------------------------------------------------


def test_item2_router_drift_between_proposal_and_apply(tmp_path):
    """Changing router configuration after proposal creation blocks apply."""
    from unittest.mock import MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content.contracts import ApplyResult

    user = _make_user(is_superuser=True, username="item2drift")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert r.ok
    # Change routing to a different provider.
    router.resolve_provider.return_value = "different-provider"
    ap = service.apply_change_request(r.request_id, user=user, expected_version=r.request_version)
    assert not ap.ok
    assert ap.error.code == "operations.provider_route_changed"


# ---------------------------------------------------------------------------
# Item 9: duplicate targets during validation
# ---------------------------------------------------------------------------


def test_item9_duplicate_targets_rejected_at_proposal():
    user = _make_user(is_superuser=True, username="item9dup")
    service = _make_service()
    result = service.create_change_request(
        user=user,
        operations=[
            {"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}},
            {"kind": "update", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}, "expected_hash": "abc"},
        ],
        provider_name="flatfile",
    )
    assert not result.ok
    assert result.error.code == "operations.duplicate_target"


# ---------------------------------------------------------------------------
# Item 17: structured lock-timeout errors
# ---------------------------------------------------------------------------


def test_item17_lock_timeout_returns_busy(tmp_path):
    """A TimeoutError from request_lock is mapped to operations.busy."""
    from unittest.mock import patch, MagicMock
    from contextlib import contextmanager
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore
    from cauldron_content.contracts import ApplyResult

    user = _make_user(is_superuser=True, username="item17busy")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=False, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert r.ok

    @contextmanager
    def _timeout(*a, **kw):
        raise TimeoutError("simulated")
        yield

    with patch("cauldron_content_operations.service.request_lock", _timeout):
        result = service.apply_change_request(r.request_id, user=user, expected_version=1)
    assert not result.ok
    assert result.error.code == "operations.busy"


# ---------------------------------------------------------------------------
# Item 18: cleanup on pre-durability failure
# ---------------------------------------------------------------------------


def test_item18_audit_insert_failure_cleans_workspace(tmp_path):
    """If the audit event insert fails, the workspace changeset is cleaned up."""
    from unittest.mock import patch, MagicMock
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_content_operations.audit import AuditSequenceError
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item18aud")
    ws_root = tmp_path / "ws"
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=ws_root))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    from cauldron_content.contracts import ApplyResult
    router.apply.return_value = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    # Patch audit append to raise on every call to simulate failure after
    # workspace create but before durable SQL record.
    with patch(
        "cauldron_content_operations.service.append_audit_event",
        side_effect=AuditSequenceError("simulated"),
    ):
        try:
            service.create_change_request(
                user=user,
                operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
                provider_name="",
            )
        except AuditSequenceError:
            pass
    # No durable ContentChangeRequest, and workspace changeset was cleaned up.
    assert ContentChangeRequest.objects.count() == 0
    change_sets_dir = ws_root / "change-sets"
    if change_sets_dir.exists():
        assert list(change_sets_dir.iterdir()) == []


def test_item14_reject_transitions_workspace_state(tmp_path):
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore, ChangeSetState
    from unittest.mock import MagicMock

    user = _make_user(is_superuser=True, username="item14reject")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=False, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="",
    )
    assert r.ok
    rej = service.reject_change_request(r.request_id, user=user, expected_version=1)
    assert rej.ok
    cs_id = _get_ws_cs_id(r.request_id)
    assert ws.get_state(cs_id) == ChangeSetState.REJECTED


# ---------------------------------------------------------------------------
# Item 1: approval must require workspace + valid payload hash
# ---------------------------------------------------------------------------


def test_item1_approval_without_workspace_denied(tmp_path):
    """Approval must fail closed when the workspace is not configured."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent
    from cauldron_content_operations.audit import AuditEventType

    user = _make_user(is_superuser=True, username="item1_appr_noworkspace")
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=None, config=cfg)

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-noworkspace-appr",
        provider_name="flatfile",
        lifecycle_state="validated",
        request_version=1,
        payload_hash="a" * 64,
        created_by=user,
    )
    ap = service.approve_change_request(cr.request_id, user=user, expected_version=1)
    assert not ap.ok
    assert ap.error.code == "workspace.unavailable"
    # Lifecycle unchanged.
    cr.refresh_from_db()
    assert cr.lifecycle_state == "validated"
    assert cr.request_version == 1
    # Bounded approval-denied audit event.
    denied = ContentAuditEvent.objects.filter(
        change_request=cr,
        event_type=AuditEventType.APPROVAL_DENIED,
    ).order_by("-sequence").first()
    assert denied is not None
    assert denied.detail.get("error_code") == "workspace.unavailable"


def test_item1_approval_missing_payload_hash_denied(tmp_path):
    """Empty/invalid payload_hash → workspace.payload_integrity_unavailable."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item1_appr_nohash")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-nohash-appr",
        provider_name="flatfile",
        lifecycle_state="validated",
        request_version=1,
        payload_hash="",  # missing
        created_by=user,
    )
    ap = service.approve_change_request(cr.request_id, user=user, expected_version=1)
    assert not ap.ok
    assert ap.error.code == "workspace.payload_integrity_unavailable"
    cr.refresh_from_db()
    assert cr.lifecycle_state == "validated"


def test_item1_approval_invalid_payload_hash_denied(tmp_path):
    """Non-hex or wrong-length payload_hash → workspace.payload_integrity_unavailable."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest
    from cauldron_workspace_flatfile.config import WorkspaceConfig
    from cauldron_workspace_flatfile.store import ChangeSetStore

    user = _make_user(is_superuser=True, username="item1_appr_badhash")
    ws = ChangeSetStore(WorkspaceConfig(workspace_root=tmp_path / "ws"))
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=ws, config=cfg)

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-badhash-appr",
        provider_name="flatfile",
        lifecycle_state="validated",
        request_version=1,
        payload_hash="notavalidhex",
        created_by=user,
    )
    ap = service.approve_change_request(cr.request_id, user=user, expected_version=1)
    assert not ap.ok
    assert ap.error.code == "workspace.payload_integrity_unavailable"


# ---------------------------------------------------------------------------
# Item 13: identifier segment validation at proposal time
# ---------------------------------------------------------------------------


def test_item13_traversal_collection_rejected_at_proposal():
    """Collections containing traversal sequences are rejected before any I/O."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig
    from cauldron_content_operations.models import ContentChangeRequest

    user = _make_user(is_superuser=True, username="item13_col")
    workspace = MagicMock()
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    cr_before = ContentChangeRequest.objects.count()
    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "../etc", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
    )
    assert not r.ok
    assert r.error.code == "operations.invalid_collection"
    # Nothing persisted.
    assert ContentChangeRequest.objects.count() == cr_before


def test_item13_absolute_slug_rejected_at_proposal():
    """Slugs that look like absolute paths are rejected before any I/O."""
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import ContentOperationsConfig

    user = _make_user(is_superuser=True, username="item13_slug")
    workspace = MagicMock()
    router = MagicMock()
    router.resolve_provider.return_value = "flatfile"
    cfg = ContentOperationsConfig(require_approval=True, allow_self_approval=True, max_operations_per_change_set=10)
    service = ContentOperationService(router=router, workspace=workspace, config=cfg)

    r = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "/etc/passwd", "data": {}}],
        provider_name="flatfile",
    )
    assert not r.ok
    assert r.error.code == "operations.invalid_slug"
