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

    # Build a mock workspace
    workspace = MagicMock()
    workspace.create.return_value = None
    workspace.save_result.return_value = None

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
