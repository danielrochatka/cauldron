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
    from django.utils import timezone
    defaults = dict(
        scope="admin",
        target_path="custom.css",
        proposed_content="body { color: red; }",
        description="Test proposal",
        status="proposed",
    )
    defaults.update(kwargs)
    # Satisfy DB constraints: reviewed_at required for approved/rejected/conflicted
    if defaults.get("status") in ("approved", "rejected", "conflicted") and "reviewed_at" not in defaults:
        defaults["reviewed_at"] = timezone.now()
    # Satisfy DB constraint: applied_at required for applied status
    if defaults.get("status") == "applied" and "applied_at" not in defaults:
        defaults["applied_at"] = timezone.now()
    # proposed_hash MUST always be a 64-char lowercase hex digest.
    if not defaults.get("proposed_hash"):
        defaults["proposed_hash"] = hashlib.sha256(
            defaults.get("proposed_content", "").encode("utf-8"),
        ).hexdigest()
    # base_exists MUST agree with base_hash under the new constraints:
    #   base_hash == ""     → base_exists must be False
    #   base_hash is 64 hex → base_exists must be True
    if "base_exists" not in defaults:
        defaults["base_exists"] = bool(defaults.get("base_hash", ""))
    return UIStyleChangeRequest.objects.create(**defaults)


# ---------------------------------------------------------------------------
# test_create_proposal_creates_record
# ---------------------------------------------------------------------------

def test_create_proposal_creates_record():
    """The ui.styles.create_proposal tool handler creates a DB record."""
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult
    from cauldron_ai_admin.models import UIStyleChangeRequest, UIStyleAuditEvent
    from django.test import override_settings

    user = _make_user(username="proposer")
    context = AdminAIToolContext(actor=user, run_id=str(uuid.uuid4()), correlation_id="")

    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(tmpdir)):
            result = _handle_ui_create_proposal(
                context,
                scope="admin",
                target_path="custom.css",
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
    assert proposal.target_path == "custom.css"
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
    from django.test import override_settings

    user = _make_user(username="proposer2")
    context = AdminAIToolContext(actor=user, run_id=str(uuid.uuid4()), correlation_id="")

    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(tmpdir)):
            result = _handle_ui_create_proposal(
                context,
                scope="pages",
                target_path="theme.css",
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
            target_path="custom.css",
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


# ---------------------------------------------------------------------------
# base_exists / base_hash — new persisted state contract
# ---------------------------------------------------------------------------


def test_base_exists_false_for_new_file():
    """A proposal for a file that does not exist has base_exists=False and empty base_hash."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    user = _make_user(username="new-file-proposer")
    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            proposal = svc.create_proposal(
                scope="admin",
                target_path="never-existed.css",
                proposed_content="body { color: mauve; }",
                description="new",
                created_by=user,
            )
    assert proposal.base_exists is False
    assert proposal.base_hash == ""
    assert len(proposal.proposed_hash) == 64


def test_base_exists_true_for_existing_file():
    """A proposal for an existing file captures a 64-char base_hash."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    user = _make_user(username="existing-file-proposer")
    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        admin_dir = Path(tmpdir) / "admin"
        admin_dir.mkdir(parents=True)
        (admin_dir / "already.css").write_text(
            "body { color: chartreuse; }", encoding="utf-8",
        )
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            proposal = svc.create_proposal(
                scope="admin",
                target_path="already.css",
                proposed_content="body { color: red; }",
                description="update",
                created_by=user,
            )
    assert proposal.base_exists is True
    assert len(proposal.base_hash) == 64
    assert all(c in "0123456789abcdef" for c in proposal.base_hash)


def test_scope_prefixed_path_rejected():
    """target_path='admin/foo.css' under scope='admin' is a validation error."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            with pytest.raises(ValueError, match="scope"):
                svc.create_proposal(
                    scope="admin",
                    target_path="admin/foo.css",
                    proposed_content="body {}",
                    description="bad",
                )


def test_cross_scope_path_rejected():
    """target_path='pages/foo.css' under scope='admin' is a validation error."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            with pytest.raises(ValueError, match="scope"):
                svc.create_proposal(
                    scope="admin",
                    target_path="pages/foo.css",
                    proposed_content="body {}",
                    description="bad",
                )


def test_store_unavailable_fails_closed(settings):
    """If neither CAULDRON_UI_OVERRIDES_DIR nor BASE_DIR is set, fail closed."""
    from cauldron_ai_admin.style_service import UIStyleChangeService

    if hasattr(settings, "CAULDRON_UI_OVERRIDES_DIR"):
        del settings.CAULDRON_UI_OVERRIDES_DIR
    # Wipe BASE_DIR too so the store cannot fall back to a default location.
    if hasattr(settings, "BASE_DIR"):
        del settings.BASE_DIR
    svc = UIStyleChangeService()
    with pytest.raises(ValueError):
        svc.create_proposal(
            scope="admin",
            target_path="somewhere.css",
            proposed_content="body {}",
            description="",
        )


def test_proposed_hash_is_always_64hex():
    """proposed_hash is always a 64-char lowercase hex string."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            for content in ("body {}", "", "/* empty */"):
                p = svc.create_proposal(
                    scope="pages",
                    target_path=f"hash-{hash(content)}.css",
                    proposed_content=content,
                    description="",
                )
                assert len(p.proposed_hash) == 64
                assert all(c in "0123456789abcdef" for c in p.proposed_hash)


def test_concurrent_approve_idempotent():
    """Two simultaneous approves — only one succeeds; the second raises."""
    from cauldron_ai_admin.style_service import UIStyleChangeService

    svc = UIStyleChangeService()
    user = _make_user(username="approve-race")
    proposal = _make_proposal(status="proposed")
    svc.approve(proposal, reviewed_by=user)
    with pytest.raises(ValueError):
        svc.approve(proposal, reviewed_by=user)


def test_apply_uses_persisted_base_state():
    """apply() must derive its expected hash from base_hash / base_exists
    on the model — never from a fresh filesystem read."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.test import override_settings

    user = _make_user(username="apply-persisted")
    svc = UIStyleChangeService()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Snapshot the file at proposal-creation time — file is empty on disk
        # but we deliberately record base_exists=False in the model.
        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="fresh.css",
            proposed_content="body { color: red; }",
            base_exists=False,
            base_hash="",
        )
        # Now something wrote a file at that path between proposal creation
        # and apply. A naive fresh-read would use its hash; the correct
        # behaviour is to use the persisted ABSENT witness → HashConflictError.
        admin_dir = Path(tmpdir) / "admin"
        admin_dir.mkdir(parents=True)
        (admin_dir / "fresh.css").write_text("intruder", encoding="utf-8")
        with override_settings(CAULDRON_UI_OVERRIDES_DIR=tmpdir):
            from cauldron_django_admin.override_store import HashConflictError
            with pytest.raises(HashConflictError):
                svc.apply(proposal, applied_by=user)
        proposal.refresh_from_db()
        assert proposal.status == "conflicted"
        assert proposal.error_code == "HASH_CONFLICT"


# ---------------------------------------------------------------------------
# Pages-style publication lifecycle (acceptance criteria 1, 10, 14)
# ---------------------------------------------------------------------------

_FAKE_HASH = "a" * 64  # valid 64-char hex digest satisfying uiscr_proposed_hash_format


def test_admin_scope_apply_is_direct_not_via_changeset():
    """For scope='admin', UIStyleChangeService.apply() writes directly to the
    UIOverrideStore without creating a SiteChangeSet."""
    from cauldron_ai_admin.style_service import UIStyleChangeService

    actor = _make_user(username="admin-scope-actor", perms=[])
    svc = UIStyleChangeService()
    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}
    fake_store.write_file_atomic.return_value = _FAKE_HASH

    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="admin",
            target_path="overrides.css",
            proposed_content=".foo { color: red; }",
            description="Test admin override",
        )
        approved = svc.approve(proposal, reviewed_by=actor)
        result = svc.apply(approved, applied_by=actor)

    assert result.status == "applied"
    fake_store.write_file_atomic.assert_called_once()


def test_mark_style_applied_transitions_to_applied():
    """mark_style_applied() transitions an approved pages proposal to applied
    (DB-only — the CSS source was already written atomically in publish() Step 2.5).
    No UIOverrideStore write should happen here."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest

    reviewer = _make_user(username="pages-reviewer", perms=[])
    svc = UIStyleChangeService()
    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}

    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages",
            target_path="90-site.css",
            proposed_content="nav { display: flex; }",
            description="Pages nav proposal",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)
        svc.mark_style_applied(
            request_id=str(approved.request_id),
            changeset_id="changeset-xyz",
            committed_hash=_FAKE_HASH,
        )

    fresh = UIStyleChangeRequest.objects.get(pk=approved.pk)
    assert fresh.status == "applied"
    assert fresh.proposed_hash == _FAKE_HASH
    # mark_style_applied is now DB-only — no filesystem write
    fake_store.write_file_atomic.assert_not_called()


def test_mark_style_applied_without_committed_hash():
    """mark_style_applied() with no committed_hash still transitions to applied;
    proposed_hash is not updated when hash is empty."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest

    reviewer = _make_user(username="pages-reviewer-nohash", perms=[])
    svc = UIStyleChangeService()
    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}

    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages",
            target_path="90-site.css",
            proposed_content="nav {}",
            description="No committed hash test",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)
        svc.mark_style_applied(
            request_id=str(approved.request_id),
            changeset_id="changeset-nohash",
        )

    fresh = UIStyleChangeRequest.objects.get(pk=approved.pk)
    assert fresh.status == "applied"


def test_mark_style_applied_conflict_is_no_longer_possible():
    """mark_style_applied is now DB-only; HashConflictError from UIOverrideStore
    is no longer triggered here (the conflict is detected in publish() Step 2.5
    before any live mutation)."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest

    reviewer = _make_user(username="hash-conflict-reviewer", perms=[])
    svc = UIStyleChangeService()
    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}

    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages",
            target_path="90-site.css",
            proposed_content="nav { display: flex; }",
            description="Stale hash test",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)
        # mark_style_applied never calls write_file_atomic — so no HashConflictError
        svc.mark_style_applied(
            request_id=str(approved.request_id),
            changeset_id="changeset-stale",
        )

    fresh = UIStyleChangeRequest.objects.get(pk=approved.pk)
    # Applied successfully — no conflict from this path
    assert fresh.status == "applied"


# ---------------------------------------------------------------------------
# Blocker 5: apply() refuses pages-scope proposals (direct bypass closed)
# ---------------------------------------------------------------------------

def test_apply_raises_for_pages_scope():
    """UIStyleChangeService.apply() raises ValueError for pages-scope proposals.
    Pages proposals must go through the Astro changeset workflow (Review &
    Preview → SiteChangeSet.publish()); direct apply is refused so the atomic
    pre-promotion CSS source write in publish() Step 2.5 is never bypassed."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest

    reviewer = _make_user(username="apply-pages-reviewer", perms=[])
    applier = _make_user(username="apply-pages-applier", perms=[])

    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}

    svc = UIStyleChangeService()
    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages",
            target_path="90-site.css",
            proposed_content="nav {}",
            description="Pages apply bypass test",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)

    with pytest.raises(ValueError, match="pages-scope"):
        svc.apply(approved, applied_by=applier)

    # Proposal must remain approved — apply didn't touch it
    approved.refresh_from_db()
    assert approved.status == "approved"


def test_http_apply_action_blocked_for_pages_scope():
    """POST action=apply to style-detail view returns an error message for
    pages-scope proposals — the button should not appear, but the view also
    guards against direct HTTP POST manipulation."""
    from cauldron_ai_admin.models import UIStyleChangeRequest
    from cauldron_ai_admin.style_service import UIStyleChangeService

    approver = _make_user(
        username="pages-http-apply-approver",
        perms=["cauldron_ai_admin.approve_ui_style_changes",
               "cauldron_ai_admin.view_ui_styles"],
    )

    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}
    svc = UIStyleChangeService()
    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages", target_path="90-site.css",
            proposed_content="nav {}", description="HTTP apply test",
        )
        approved = svc.approve(proposal, reviewed_by=approver)

    from django.urls import reverse
    url = reverse("cauldron_ai_admin:style-detail", kwargs={"request_id": approved.request_id})
    client = Client()
    client.force_login(approver)
    response = client.post(
        url,
        {"action": "apply"},
        follow=True,
    )

    assert response.status_code == 200
    # Proposal status must NOT have changed to applied
    approved.refresh_from_db()
    assert approved.status == "approved"


# ---------------------------------------------------------------------------
# Blocker 6 + 7: StylePublicationPrepareView GET/reuse (uses both DB tables)
# ---------------------------------------------------------------------------

def test_style_prepare_view_get_does_not_create_changeset():
    """GET on StylePublicationPrepareView must not create a SiteChangeSet —
    it should only redirect back (no changeset creation on GET)."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.apps import apps
    from django.urls import reverse as django_reverse

    SiteChangeSet = apps.get_model("cauldron_site_astro", "SiteChangeSet")

    user = _make_user(
        username="get-nocs-user",
        perms=["cauldron_content_operations.apply_content_changes",
               "cauldron_ai_admin.view_ui_styles"],
    )
    reviewer = _make_user(username="get-nocs-reviewer", perms=[])

    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}
    svc = UIStyleChangeService()
    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages", target_path="90-site.css",
            proposed_content="nav {}", description="get test",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)

    initial_count = SiteChangeSet.objects.count()

    url = django_reverse(
        "cauldron_site_astro:style-prepare",
        kwargs={"request_id": str(approved.request_id)},
    )
    client = Client()
    client.force_login(user)
    client.get(url, follow=False)

    # No new SiteChangeSet should have been created
    assert SiteChangeSet.objects.count() == initial_count


def test_style_prepare_view_post_reuses_draft_ready_changeset():
    """When the proposal already has a DRAFT_READY SiteChangeSet linked via
    site_changeset_id, POST to StylePublicationPrepareView redirects to it
    without creating a new SiteChangeSet."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from django.apps import apps
    from django.urls import reverse as django_reverse
    import uuid as _uuid

    SiteChangeSet = apps.get_model("cauldron_site_astro", "SiteChangeSet")

    user = _make_user(
        username="reuse-dr-user",
        perms=["cauldron_content_operations.apply_content_changes",
               "cauldron_ai_admin.view_ui_styles"],
    )
    reviewer = _make_user(username="reuse-dr-reviewer", perms=[])

    fake_store = MagicMock()
    fake_store.inspect_state.return_value = {"exists": False, "hash": None, "size": 0}
    svc = UIStyleChangeService()
    with patch("cauldron_ai_admin.style_service._get_override_store", return_value=fake_store):
        proposal = svc.create_proposal(
            scope="pages", target_path="90-site.css",
            proposed_content="nav {}", description="reuse test",
        )
        approved = svc.approve(proposal, reviewed_by=reviewer)

    existing_cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
        style_request_id=str(approved.request_id),
    )
    approved.site_changeset_id = str(existing_cs.id)
    approved.save(update_fields=["site_changeset_id"])

    initial_count = SiteChangeSet.objects.count()

    url = django_reverse(
        "cauldron_site_astro:style-prepare",
        kwargs={"request_id": str(approved.request_id)},
    )
    client = Client()
    client.force_login(user)

    with patch("cauldron_site_astro.views.reverse", return_value=f"/review/{existing_cs.id}/"):
        response = client.post(url, follow=False)

    # No new changeset created
    assert SiteChangeSet.objects.count() == initial_count
    # Redirects (302) — the existing changeset was found
    assert response.status_code == 302
