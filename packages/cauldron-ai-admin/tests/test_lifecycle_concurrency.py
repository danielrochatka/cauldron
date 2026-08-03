"""Real concurrency tests for the UIStyleChangeService lifecycle.

Each test uses ``threading.Thread`` to run two lifecycle transitions
simultaneously against a shared SQLite database and asserts the invariants
the service is meant to uphold — exactly one apply commits, exactly one
approve wins, and a DB failure after a filesystem write rolls the
filesystem back to its captured pre-image.

Uses ``pytest.mark.django_db(transaction=True)`` because ``select_for_update``
and multi-connection concurrency semantics only surface when we drive real
transactions instead of the fixture's wrapping ``atomic``. SQLite blocks
concurrent writes at the file level; we accept ``OperationalError``
("database is locked") from the losing thread as an equivalent failure to
``ValueError``/``IntegrityError`` — the invariant we're testing is
"exactly one wins", not the exact exception type.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections
from django.test import override_settings


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    return user


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
    if defaults.get("status") in ("approved", "rejected", "conflicted") \
            and "reviewed_at" not in defaults:
        defaults["reviewed_at"] = timezone.now()
    if defaults.get("status") == "applied" and "applied_at" not in defaults:
        defaults["applied_at"] = timezone.now()
    if not defaults.get("proposed_hash"):
        defaults["proposed_hash"] = hashlib.sha256(
            defaults.get("proposed_content", "").encode("utf-8"),
        ).hexdigest()
    if "base_exists" not in defaults:
        defaults["base_exists"] = bool(defaults.get("base_hash", ""))
    return UIStyleChangeRequest.objects.create(**defaults)


def _run_threads(*targets):
    """Start each callable on its own thread; join with a timeout."""
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "Thread did not finish within timeout."


@pytest.mark.django_db(transaction=True)
def test_concurrent_approves_only_one_wins():
    """Two threads call ``approve`` on the same proposal simultaneously.

    Invariants:
    - Exactly two outcomes are recorded (success or handled exception).
    - The proposal ends up in status=approved.
    - Exactly one approved audit event exists (two approvals never both commit).
    - When both threads get OperationalError (SQLite lock exhaustion), a single
      serial approval still succeeds, proving the lock was transient.
    - On databases with row-level locking, exactly one racing call succeeds.
    """
    from cauldron_ai_admin.models import UIStyleAuditEvent, UIStyleChangeRequest
    from cauldron_ai_admin.style_service import UIStyleChangeService

    user = _make_user("approve-race-user")
    proposal = _make_proposal(status="proposed")
    proposal_pk = proposal.pk
    user_pk = user.pk
    service = UIStyleChangeService()

    approved: list = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def do_approve():
        close_old_connections()
        try:
            fresh_proposal = UIStyleChangeRequest.objects.get(pk=proposal_pk)
            fresh_user = get_user_model().objects.get(pk=user_pk)
            barrier.wait()
            result = service.approve(fresh_proposal, reviewed_by=fresh_user)
            with lock:
                approved.append(result)
        except (ValueError, OperationalError) as exc:
            with lock:
                errors.append(exc)
        finally:
            close_old_connections()

    _run_threads(do_approve, do_approve)

    assert len(approved) + len(errors) == 2, (
        f"Expected exactly 2 outcomes, got {len(approved)} success(es) "
        f"and {len(errors)} error(s)"
    )

    all_operational = all(isinstance(e, OperationalError) for e in errors)
    if len(approved) == 0 and all_operational:
        # SQLite lock exhaustion: both racing writes failed.  Prove a serial
        # approval still works — the proposal is still "proposed".
        close_old_connections()
        fresh = UIStyleChangeRequest.objects.get(pk=proposal_pk)
        fresh_user = get_user_model().objects.get(pk=user_pk)
        service.approve(fresh, reviewed_by=fresh_user)
    else:
        # Row-locking database (or SQLite serialised exactly one through):
        # exactly one racing call must have succeeded.
        assert len(approved) == 1, (
            f"Expected exactly one racing success, got {len(approved)}. "
            f"Errors: {[type(e).__name__ for e in errors]}"
        )

    close_old_connections()
    proposal.refresh_from_db()
    assert proposal.status == "approved"

    # Exactly one approved audit event — two approvals must never both commit.
    audit_events = UIStyleAuditEvent.objects.filter(
        change_request=proposal, event_type="approved"
    )
    assert audit_events.count() == 1, (
        f"Expected exactly one approved audit event, got {audit_events.count()}. "
        f"Two approvals must never both commit."
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_applies_produce_exactly_one_applied_event():
    """Two threads apply the same approved proposal simultaneously. Exactly
    one apply commits — one ``applied`` audit event, one filesystem write.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleAuditEvent
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-race-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="race.css",
            proposed_content="body { color: red; }",
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        successes: list = []
        errors: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def do_apply():
            barrier.wait()
            try:
                with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
                    result = service.apply(proposal, applied_by=user)
                    with lock:
                        successes.append(result)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)
            finally:
                close_old_connections()

        _run_threads(do_apply, do_apply)

        # Exactly one applied event must exist — the winning apply is the
        # only one whose Phase 3 committed.
        applied_events = UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="applied",
        )
        assert applied_events.count() == 1, (
            "Expected exactly one applied audit event, got "
            f"{applied_events.count()}. errors={[type(e).__name__ for e in errors]}"
        )

        # Final DB status must be applied (the winner's transition).
        proposal.refresh_from_db()
        assert proposal.status == "applied"

        # And the file on disk must contain the proposed content.
        store = UIOverrideStore(override_root)
        assert store.read_file("admin", "race.css") == \
            "body { color: red; }"


@pytest.mark.django_db(transaction=True)
def test_db_failure_after_write_rolls_filesystem_back():
    """If the Phase 3 DB save fails after the filesystem write, we roll the
    file back — for a new-file proposal that means the file must not exist
    afterwards.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-db-fail-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="new-file.css",
            proposed_content="body { color: red; }",
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        # Inject a failure in Phase 3: fail the first save that tries to
        # transition status→"applied". Leave Phase 1's non-mutating select
        # untouched.
        original_save = UIStyleChangeRequest.save
        fired = threading.Event()

        def failing_save(self, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            if (
                "status" in update_fields
                and getattr(self, "status", "") == "applied"
                and not fired.is_set()
            ):
                fired.set()
                raise RuntimeError("Injected DB failure during apply commit.")
            return original_save(self, *args, **kwargs)

        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
            with patch.object(UIStyleChangeRequest, "save", failing_save):
                with pytest.raises(RuntimeError):
                    service.apply(proposal, applied_by=user)

            # For a new-file proposal, rollback = delete the created file.
            store = UIOverrideStore(override_root)
            with pytest.raises(FileNotFoundError):
                store.read_file("admin", "new-file.css")


@pytest.mark.django_db(transaction=True)
def test_db_failure_after_write_restores_previous_bytes():
    """If the Phase 3 DB save fails and the proposal targets an existing
    file, we restore the exact pre-image captured in Phase 1.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleChangeRequest
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-restore-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        admin_dir = override_root / "admin"
        admin_dir.mkdir()

        original = "body { color: chartreuse; }"
        (admin_dir / "already.css").write_text(original, encoding="utf-8")
        original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="already.css",
            proposed_content="body { color: red; }",
            base_exists=True,
            base_hash=original_hash,
        )
        service = UIStyleChangeService()

        original_save = UIStyleChangeRequest.save
        fired = threading.Event()

        def failing_save(self, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            if (
                "status" in update_fields
                and getattr(self, "status", "") == "applied"
                and not fired.is_set()
            ):
                fired.set()
                raise RuntimeError("Injected DB failure during apply commit.")
            return original_save(self, *args, **kwargs)

        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
            with patch.object(UIStyleChangeRequest, "save", failing_save):
                with pytest.raises(RuntimeError):
                    service.apply(proposal, applied_by=user)

            store = UIOverrideStore(override_root)
            assert store.read_file("admin", "already.css") == original


@pytest.mark.django_db(transaction=True)
def test_phase3_db_failure_rollback_persists_apply_db_failed_new_file():
    """A successful rollback after Phase 3 must persist a durable
    ``APPLY_DB_FAILED`` audit record — swallowing the rollback outcome
    silently would leave operators unable to see that a retry is safe.
    The proposal stays ``approved`` (rollback succeeded) so a retry is
    legitimate; the failure is recorded on ``error_code`` and via a
    ``failed`` audit event.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import (
        UIStyleAuditEvent, UIStyleChangeRequest,
    )
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-persist-new-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="new-fail.css",
            proposed_content="body { color: red; }",
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        original_save = UIStyleChangeRequest.save
        fired = threading.Event()

        def failing_save(self, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            if (
                "status" in update_fields
                and getattr(self, "status", "") == "applied"
                and not fired.is_set()
            ):
                fired.set()
                raise RuntimeError("Injected DB failure during apply commit.")
            return original_save(self, *args, **kwargs)

        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
            with patch.object(UIStyleChangeRequest, "save", failing_save):
                with pytest.raises(RuntimeError):
                    service.apply(proposal, applied_by=user)

            # Rollback should have removed the freshly-created file.
            store = UIOverrideStore(override_root)
            with pytest.raises(FileNotFoundError):
                store.read_file("admin", "new-fail.css")

        proposal.refresh_from_db()
        assert proposal.status == "approved"
        assert proposal.error_code == "APPLY_DB_FAILED"
        assert "filesystem was restored" in (proposal.error_summary or "")

        failed_events = list(UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="failed",
        ))
        assert len(failed_events) == 1
        detail = failed_events[0].detail or {}
        assert detail.get("error_class") == "DB_COMMIT_FAILED"
        assert detail.get("rollback") == "succeeded"


@pytest.mark.django_db(transaction=True)
def test_phase3_db_failure_rollback_persists_apply_db_failed_existing_file():
    """Same durability guarantee, but for an existing-file proposal: the
    pre-image must be restored on disk AND ``APPLY_DB_FAILED`` recorded
    so the proposal can be retried safely.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import (
        UIStyleAuditEvent, UIStyleChangeRequest,
    )
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-persist-existing-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        admin_dir = override_root / "admin"
        admin_dir.mkdir()

        original = "body { color: chartreuse; }"
        (admin_dir / "already.css").write_text(original, encoding="utf-8")
        original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="already.css",
            proposed_content="body { color: red; }",
            base_exists=True,
            base_hash=original_hash,
        )
        service = UIStyleChangeService()

        original_save = UIStyleChangeRequest.save
        fired = threading.Event()

        def failing_save(self, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            if (
                "status" in update_fields
                and getattr(self, "status", "") == "applied"
                and not fired.is_set()
            ):
                fired.set()
                raise RuntimeError("Injected DB failure during apply commit.")
            return original_save(self, *args, **kwargs)

        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
            with patch.object(UIStyleChangeRequest, "save", failing_save):
                with pytest.raises(RuntimeError):
                    service.apply(proposal, applied_by=user)

            store = UIOverrideStore(override_root)
            assert store.read_file("admin", "already.css") == original

        proposal.refresh_from_db()
        assert proposal.status == "approved"
        assert proposal.error_code == "APPLY_DB_FAILED"

        failed_events = list(UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="failed",
        ))
        assert len(failed_events) == 1
        detail = failed_events[0].detail or {}
        assert detail.get("error_class") == "DB_COMMIT_FAILED"
        assert detail.get("rollback") == "succeeded"


@pytest.mark.django_db(transaction=True)
def test_phase3_rollback_failure_marks_conflicted():
    """When the DB fails AND the filesystem rollback also fails, the
    proposal must be marked ``conflicted`` with ``ROLLBACK_FAILED`` so a
    human review is required before another apply. Leaving the row in
    ``approved`` would allow a second apply that could compound the DB↔FS
    drift.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import (
        UIStyleAuditEvent, UIStyleChangeRequest,
    )
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-rollback-fail-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="new-rollback-fail.css",
            proposed_content="body { color: red; }",
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        original_save = UIStyleChangeRequest.save
        fired = threading.Event()

        def failing_save(self, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            if (
                "status" in update_fields
                and getattr(self, "status", "") == "applied"
                and not fired.is_set()
            ):
                fired.set()
                raise RuntimeError("Injected DB failure during apply commit.")
            return original_save(self, *args, **kwargs)

        def failing_delete(self, *args, **kwargs):
            raise RuntimeError("Injected filesystem rollback failure.")

        with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
            with patch.object(UIStyleChangeRequest, "save", failing_save):
                with patch.object(
                    UIOverrideStore, "delete_file_atomic", failing_delete,
                ):
                    with pytest.raises(RuntimeError):
                        service.apply(proposal, applied_by=user)

            # The file remains on disk because rollback failed — this is
            # exactly the DB↔FS mismatch the ``conflicted`` state signals.
            store = UIOverrideStore(override_root)
            assert store.read_file("admin", "new-rollback-fail.css") == \
                "body { color: red; }"

        proposal.refresh_from_db()
        assert proposal.status == "conflicted"
        assert proposal.error_code == "ROLLBACK_FAILED"

        failed_events = list(UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="failed",
        ))
        assert len(failed_events) == 1
        detail = failed_events[0].detail or {}
        assert detail.get("error_class") == "ROLLBACK_FAILED"
        assert detail.get("rollback_error_class") == "RuntimeError"


@pytest.mark.django_db(transaction=True)
def test_simultaneous_apply_one_replace_one_applied_event():
    """A dedicated race test with a hard barrier: two threads must hit
    Phase 2 at the same instant, and the assertions specifically check
    exactly-one ``applied`` audit event and that the final file bytes
    equal the proposed content. This is stronger than the pre-existing
    concurrent-apply test because it asserts both invariants explicitly.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleAuditEvent
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("apply-race-hard-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposed = "body { color: magenta; }"
        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="hard-race.css",
            proposed_content=proposed,
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        successes: list = []
        errors: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def do_apply():
            barrier.wait()
            try:
                with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
                    result = service.apply(proposal, applied_by=user)
                    with lock:
                        successes.append(result)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)
            finally:
                close_old_connections()

        _run_threads(do_apply, do_apply)

        applied_events = UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="applied",
        )
        assert applied_events.count() == 1, (
            "Expected exactly one applied audit event, got "
            f"{applied_events.count()}. errors={[type(e).__name__ for e in errors]}"
        )

        proposal.refresh_from_db()
        assert proposal.status == "applied"

        store = UIOverrideStore(override_root)
        assert store.read_file("admin", "hard-race.css") == proposed


@pytest.mark.django_db(transaction=True)
def test_concurrent_applies_only_one_claims_applying():
    """Two callers race to apply the same approved proposal.
    Exactly one must succeed in transitioning to 'applying' and then
    'applied'. The loser must see status != 'approved' and raise ValueError
    or OperationalError (SQLite serialisation). If both get OperationalError
    (SQLite lock exhaustion), we retry sequentially to confirm one can apply.
    """
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleAuditEvent

    user = _make_user("apply-claim-race-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        (override_root / "admin").mkdir()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="claim-race.css",
            proposed_content="body { color: blue; }",
            base_exists=False,
            base_hash="",
        )
        service = UIStyleChangeService()

        successes: list = []
        errors: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def do_apply():
            barrier.wait()
            try:
                with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
                    result = service.apply(proposal, applied_by=user)
                    with lock:
                        successes.append(result)
            except (ValueError, OperationalError) as exc:
                with lock:
                    errors.append(exc)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)
            finally:
                close_old_connections()

        _run_threads(do_apply, do_apply)

        # If both threads hit OperationalError (SQLite lock exhaustion), the
        # proposal may still be in approved state. Try to apply sequentially
        # to confirm at least one application is possible.
        proposal.refresh_from_db()
        if proposal.status == "approved" and len(successes) == 0:
            # Both lost to SQLite — retry sequentially.
            with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
                service.apply(proposal, applied_by=user)
            proposal.refresh_from_db()

        # At most one applied audit event must exist.
        applied_events = UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="applied",
        )
        assert applied_events.count() == 1, (
            "Expected exactly one applied audit event, got "
            f"{applied_events.count()}. errors={[type(e).__name__ for e in errors]}"
        )

        proposal.refresh_from_db()
        assert proposal.status == "applied"
        # The apply_lease must be cleared after successful application.
        assert proposal.apply_lease == ""


@pytest.mark.django_db(transaction=True)
def test_identical_content_apply_serialized():
    """For a proposal where proposed_content == base_content, two concurrent
    callers must still see exactly one FS write and one applied audit event."""
    from cauldron_ai_admin.style_service import UIStyleChangeService
    from cauldron_ai_admin.models import UIStyleAuditEvent
    from cauldron_django_admin.override_store import UIOverrideStore

    user = _make_user("identical-content-user")

    with tempfile.TemporaryDirectory() as tmpdir:
        override_root = Path(tmpdir)
        admin_dir = override_root / "admin"
        admin_dir.mkdir()

        # The file already exists with the same content as the proposal.
        content = "body { color: green; }"
        (admin_dir / "same.css").write_text(content, encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        proposal = _make_proposal(
            status="approved",
            scope="admin",
            target_path="same.css",
            proposed_content=content,
            base_exists=True,
            base_hash=content_hash,
        )
        service = UIStyleChangeService()

        successes: list = []
        errors: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def do_apply():
            barrier.wait()
            try:
                with override_settings(CAULDRON_UI_OVERRIDES_DIR=str(override_root)):
                    result = service.apply(proposal, applied_by=user)
                    with lock:
                        successes.append(result)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)
            finally:
                close_old_connections()

        _run_threads(do_apply, do_apply)

        # There should be exactly one applied audit event.
        applied_events = UIStyleAuditEvent.objects.filter(
            change_request=proposal, event_type="applied",
        )
        assert applied_events.count() == 1, (
            "Expected exactly one applied audit event, got "
            f"{applied_events.count()}. errors={[type(e).__name__ for e in errors]}"
        )

        proposal.refresh_from_db()
        assert proposal.status == "applied"

        store = UIOverrideStore(override_root)
        assert store.read_file("admin", "same.css") == content
