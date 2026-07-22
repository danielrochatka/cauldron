"""Tests for operational models."""
import pytest

pytestmark = pytest.mark.django_db


def test_change_request_creation():
    from django.contrib.auth import get_user_model
    from cauldron_content_operations.models import ContentChangeRequest
    User = get_user_model()
    user = User.objects.create_user(username="testuser", password="password")
    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-1",
        provider_name="flatfile",
        created_by=user,
    )
    assert cr.request_id
    assert cr.lifecycle_state == "proposed"
    assert cr.request_version == 1


def test_audit_event_sequence_constraint():
    from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest
    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-2",
        provider_name="flatfile",
    )
    from cauldron_content_operations.audit import append_audit_event
    e1 = append_audit_event(
        change_request=cr,
        event_type="test.event",
        resulting_state="proposed",
    )
    e2 = append_audit_event(
        change_request=cr,
        event_type="test.event2",
        resulting_state="validated",
    )
    assert e1.sequence == 1
    assert e2.sequence == 2


def test_idempotency_key_unique_per_creator():
    """The idempotency_key uniqueness is scoped to the creating user."""
    from django.contrib.auth import get_user_model
    from django.db import IntegrityError
    from cauldron_content_operations.models import ContentChangeRequest

    User = get_user_model()
    u1 = User.objects.create_user(username="idem_a", password="pw")
    u2 = User.objects.create_user(username="idem_b", password="pw")

    ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-3",
        provider_name="flatfile",
        idempotency_key="unique-key-1",
        created_by=u1,
    )
    # Same key + same creator should still collide.
    with pytest.raises(IntegrityError):
        ContentChangeRequest.objects.create(
            workspace_changeset_id="cs-4",
            provider_name="flatfile",
            idempotency_key="unique-key-1",
            created_by=u1,
        )


def test_idempotency_key_not_shared_across_creators():
    """Different creators may reuse the same idempotency key."""
    from django.contrib.auth import get_user_model
    from cauldron_content_operations.models import ContentChangeRequest

    User = get_user_model()
    u1 = User.objects.create_user(username="idem_c", password="pw")
    u2 = User.objects.create_user(username="idem_d", password="pw")
    ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-5",
        provider_name="flatfile",
        idempotency_key="shared-key",
        created_by=u1,
    )
    # Should succeed (different creator).
    ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-6",
        provider_name="flatfile",
        idempotency_key="shared-key",
        created_by=u2,
    )


def test_auth_user_model_respected():
    """AUTH_USER_MODEL is used for FK relationships."""
    from django.conf import settings
    assert settings.AUTH_USER_MODEL == "auth.User"
    from cauldron_content_operations.models import ContentChangeRequest
    from django.contrib.auth import get_user_model
    User = get_user_model()
    assert ContentChangeRequest.created_by.field.related_model is User
