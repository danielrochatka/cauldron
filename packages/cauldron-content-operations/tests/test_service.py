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
    user = _make_user(is_superuser=True)
    service = _make_service()
    r1 = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p1", "slug": "p1", "data": {}}],
        provider_name="flatfile",
        idempotency_key="my-key-1",
    )
    assert r1.ok
    r2 = service.create_change_request(
        user=user,
        operations=[{"kind": "create", "collection": "pages", "item_id": "p2", "slug": "p2", "data": {}}],
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
    result = service.approve_change_request(r.request_id, user=user)
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
        "view_published_content", "view_draft_content", "propose_content_changes",
        "validate_content_changes", "approve_content_changes", "reject_content_changes",
        "apply_content_changes", "rollback_content_changes", "view_content_audit",
    ]
    for codename in codenames:
        assert Permission.objects.filter(codename=codename).exists(), f"Missing permission: {codename}"
