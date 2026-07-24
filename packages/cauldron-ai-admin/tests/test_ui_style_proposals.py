"""Tests for UIStyleChangeRequest model, tools, and views."""
import hashlib
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client

pytestmark = pytest.mark.django_db


def _make_user(*, username, perms=()):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    user.set_password("pw")
    user.save()
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _make_proposal(**kwargs):
    from cauldron_ai_admin.models import UIStyleChangeRequest
    defaults = dict(
        scope="admin",
        target_path="admin/custom.css",
        proposed_content="body { color: red; }",
        description="Test proposal",
        status="proposed",
    )
    defaults.update(kwargs)
    return UIStyleChangeRequest.objects.create(**defaults)


# ---------------------------------------------------------------------------
# test_create_proposal_creates_record
# ---------------------------------------------------------------------------

def test_create_proposal_creates_record():
    """The ui.styles.create_proposal tool handler creates a DB record."""
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult
    from cauldron_ai_admin.models import UIStyleChangeRequest, UIStyleAuditEvent

    user = _make_user(username="proposer")
    context = AdminAIToolContext(actor=user, run_id=str(uuid.uuid4()), correlation_id="")

    result = _handle_ui_create_proposal(
        context,
        scope="admin",
        target_path="admin/custom.css",
        proposed_content="body { color: red; }",
        description="Make body red",
    )

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    assert "request_id" in result.data
    assert result.data["status"] == "proposed"

    request_id = result.data["request_id"]
    proposal = UIStyleChangeRequest.objects.get(request_id=request_id)
    assert proposal.scope == "admin"
    assert proposal.target_path == "admin/custom.css"
    assert proposal.status == "proposed"
    assert proposal.proposed_content == "body { color: red; }"

    # Audit event should be created
    audit = UIStyleAuditEvent.objects.filter(change_request=proposal)
    assert audit.count() == 1
    assert audit.first().event_type == "proposed"


# ---------------------------------------------------------------------------
# test_ai_cannot_apply_own_proposal
# ---------------------------------------------------------------------------

def test_ai_cannot_apply_own_proposal():
    """The create_proposal tool only creates with status=proposed, never applies."""
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult
    from cauldron_ai_admin.models import UIStyleChangeRequest

    user = _make_user(username="proposer2")
    context = AdminAIToolContext(actor=user, run_id=str(uuid.uuid4()), correlation_id="")

    result = _handle_ui_create_proposal(
        context,
        scope="pages",
        target_path="pages/theme.css",
        proposed_content=".hero { background: blue; }",
        description="Blue hero",
    )

    assert isinstance(result, AdminAIToolResult)
    request_id = result.data["request_id"]
    proposal = UIStyleChangeRequest.objects.get(request_id=request_id)
    # Must remain in proposed state — the tool never transitions to applied
    assert proposal.status == "proposed"
    assert proposal.applied_at is None


# ---------------------------------------------------------------------------
# test_proposal_approval
# ---------------------------------------------------------------------------

def test_proposal_approval():
    """POST action=approve changes proposal status to approved."""
    user = _make_user(
        username="approver",
        perms=(
            "cauldron_ai_admin.view_ui_styles",
            "cauldron_ai_admin.approve_ui_style_changes",
        ),
    )
    proposal = _make_proposal(status="proposed")

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})
    response = client.post(url, data={"action": "approve"})

    assert response.status_code in (302, 200)
    proposal.refresh_from_db()
    assert proposal.status == "approved"
    assert proposal.reviewed_by == user
    assert proposal.reviewed_at is not None


# ---------------------------------------------------------------------------
# test_proposal_rejection
# ---------------------------------------------------------------------------

def test_proposal_rejection():
    """POST action=reject changes proposal status to rejected."""
    user = _make_user(
        username="rejecter",
        perms=(
            "cauldron_ai_admin.view_ui_styles",
            "cauldron_ai_admin.approve_ui_style_changes",
        ),
    )
    proposal = _make_proposal(status="proposed")

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})
    response = client.post(url, data={"action": "reject"})

    assert response.status_code in (302, 200)
    proposal.refresh_from_db()
    assert proposal.status == "rejected"
    assert proposal.reviewed_by == user
    assert proposal.reviewed_at is not None


# ---------------------------------------------------------------------------
# test_conflict_on_apply
# ---------------------------------------------------------------------------

def test_conflict_on_apply():
    """When base_hash doesn't match the current file hash, status becomes conflicted."""
    user = _make_user(
        username="apply-user",
        perms=(
            "cauldron_ai_admin.view_ui_styles",
            "cauldron_ai_admin.approve_ui_style_changes",
        ),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        override_dir = Path(tmpdir)
        # Write initial file
        css_dir = override_dir / "admin"
        css_dir.mkdir(parents=True)
        css_file = css_dir / "custom.css"
        original_content = "body { color: blue; }"
        css_file.write_text(original_content, encoding="utf-8")

        # Compute real hash
        real_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
        wrong_hash = "0" * 64  # Deliberately wrong

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="admin/custom.css",
            proposed_content="body { color: red; }",
            base_hash=wrong_hash,
        )

        from django.urls import reverse
        client = Client()
        client.force_login(user)
        url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})

        from django.test import override_settings
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_dir)):
            response = client.post(url, data={"action": "apply"})

        assert response.status_code in (302, 200)
        proposal.refresh_from_db()
        assert proposal.status == "conflicted"
        assert proposal.error_code == "HASH_CONFLICT"


# ---------------------------------------------------------------------------
# test_audit_events_persisted
# ---------------------------------------------------------------------------

def test_audit_events_persisted():
    """Audit events are created on state changes (approve, then reject sequence check)."""
    from cauldron_ai_admin.models import UIStyleAuditEvent
    user = _make_user(
        username="audit-user",
        perms=(
            "cauldron_ai_admin.view_ui_styles",
            "cauldron_ai_admin.approve_ui_style_changes",
        ),
    )
    proposal = _make_proposal(status="proposed")

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})
    client.post(url, data={"action": "approve"})

    events = UIStyleAuditEvent.objects.filter(change_request=proposal).order_by("sequence")
    assert events.count() >= 1
    event_types = [e.event_type for e in events]
    assert "approved" in event_types


# ---------------------------------------------------------------------------
# test_preview_proposal
# ---------------------------------------------------------------------------

def test_preview_proposal():
    """ui.styles.preview_proposal returns a bounded content preview."""
    from cauldron_ai_admin.builtin_tools import _handle_preview_proposal
    from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult

    proposal = _make_proposal(
        proposed_content="x" * 3000,
        description="Preview test",
    )

    user = _make_user(username="previewer")
    context = AdminAIToolContext(actor=user, run_id=str(uuid.uuid4()), correlation_id="")

    result = _handle_preview_proposal(context, request_id=str(proposal.request_id))

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    assert result.data["request_id"] == str(proposal.request_id)
    assert result.data["status"] == "proposed"
    # Preview is bounded to 2000 chars
    assert len(result.data["proposed_content_preview"]) <= 2000


# ---------------------------------------------------------------------------
# test_style_list_view
# ---------------------------------------------------------------------------

def test_style_list_view():
    """GET /ui/style-changes/ returns 200 for a user with view_ui_styles."""
    user = _make_user(
        username="list-viewer",
        perms=("cauldron_ai_admin.view_ui_styles",),
    )
    _make_proposal(description="Proposal A")
    _make_proposal(description="Proposal B")

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-list")
    response = client.get(url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# test_style_detail_view
# ---------------------------------------------------------------------------

def test_style_detail_view():
    """GET /ui/style-changes/<id>/ returns 200 for a user with view_ui_styles."""
    user = _make_user(
        username="detail-viewer",
        perms=("cauldron_ai_admin.view_ui_styles",),
    )
    proposal = _make_proposal(description="Detail proposal")

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})
    response = client.get(url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# test_css_escaped_in_preview
# ---------------------------------------------------------------------------

def test_css_escaped_in_preview():
    """proposed_content is HTML-escaped in the template (no raw <script> injection)."""
    user = _make_user(
        username="xss-checker",
        perms=("cauldron_ai_admin.view_ui_styles",),
    )
    proposal = _make_proposal(
        proposed_content='</pre><script>alert(1)</script><pre>',
        description="XSS test",
    )

    from django.urls import reverse
    client = Client()
    client.force_login(user)
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": proposal.request_id})
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    # The raw <script> tag must NOT appear — it must be HTML-escaped
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content
