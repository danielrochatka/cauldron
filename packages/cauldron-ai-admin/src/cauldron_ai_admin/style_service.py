"""UIStyleChangeService — lifecycle logic for AI style proposals."""
from __future__ import annotations

import hashlib
import logging
import uuid as _uuid
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import UIStyleChangeRequest, UIStyleAuditEvent

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_override_store():
    from pathlib import Path
    from django.conf import settings
    from cauldron_django_admin.override_store import UIOverrideStore
    override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
    if override_dir is None:
        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir is None:
            raise RuntimeError("CAULDRON_UI_OVERRIDES_DIR and BASE_DIR are not configured.")
        override_dir = Path(base_dir) / "cauldron-overrides"
    return UIOverrideStore(Path(override_dir))


def _next_sequence_locked(proposal: UIStyleChangeRequest) -> int:
    """Allocate the next audit sequence for a proposal.

    Callers MUST already hold ``select_for_update()`` on the proposal row so
    that concurrent apply/approve attempts cannot race to the same sequence
    number. The unique constraint on
    ``(change_request, sequence)`` remains the last line of defence.
    """
    result = UIStyleAuditEvent.objects.filter(
        change_request=proposal,
    ).select_for_update().aggregate(Max("sequence"))
    return (result["sequence__max"] or 0) + 1


def _record_pre_read_conflict(
    proposal_pk,
    lease_token: str,
    applied_by,
    summary: str,
) -> None:
    """Transition an 'applying' proposal to 'conflicted' during pre-read.

    Called when we detect a hash mismatch while reading the current file
    content for rollback capture (before the filesystem write). The proposal
    is in 'applying' status at this point; we verify the lease to guard
    against a concurrent actor having already updated the row.
    """
    with transaction.atomic():
        locked = UIStyleChangeRequest.objects.select_for_update().get(
            pk=proposal_pk,
        )
        if locked.status == "applying" and locked.apply_lease == lease_token:
            locked.status = "conflicted"
            locked.apply_lease = ""
            locked.error_code = "HASH_CONFLICT"
            locked.error_summary = summary
            locked.save(update_fields=[
                "status", "apply_lease", "error_code", "error_summary",
            ])
            UIStyleAuditEvent.objects.create(
                change_request=locked,
                sequence=_next_sequence_locked(locked),
                event_type="conflict",
                actor=applied_by,
                detail={"error": "hash_conflict"},
            )


class RollbackResult:
    """Outcome of :func:`_attempt_filesystem_rollback`.

    ``success`` is True when the previous on-disk state was restored (or the
    freshly-written file was successfully deleted for a create). When False,
    ``error_class`` names the exception type that defeated the rollback so
    the caller can persist it for operators.
    """

    __slots__ = ("success", "error_class")

    def __init__(self, success: bool, error_class: str = "") -> None:
        self.success = success
        self.error_class = error_class


def _attempt_filesystem_rollback(
    store,
    scope: str,
    target_path: str,
    old_content: str | None,
    written_hash: str,
) -> RollbackResult:
    """Best-effort filesystem restore after a post-write DB failure.

    If we had captured the previous file content we try to restore it using
    the just-written hash as the optimistic-lock witness. If there was no
    previous content (the file was created by this apply), we try to delete
    the freshly-written file instead. Returns a :class:`RollbackResult` so
    the caller can persist a durable outcome record — swallowing rollback
    failures silently would let the DB and filesystem drift apart without
    any operator-visible signal.

    ``scope`` and ``target_path`` are passed as separate arguments (rather
    than a model instance) because the caller invokes us from an outer
    exception handler where the transaction has rolled back — captured
    primitives make the rollback contract explicit and side-effect free.
    """
    try:
        if old_content is not None:
            store.write_file_atomic(
                scope, target_path, old_content,
                expected_hash=written_hash,
            )
        else:
            store.delete_file_atomic(
                scope, target_path, expected_hash=written_hash,
            )
        return RollbackResult(success=True)
    except Exception as exc:
        logger.exception(
            "CRITICAL: filesystem write succeeded but DB failed and rollback "
            "also failed. Manual reconciliation required for scope=%r target=%r",
            scope, target_path,
        )
        return RollbackResult(success=False, error_class=type(exc).__name__)


class UIStyleChangeService:
    """Lifecycle service for UIStyleChangeRequest proposals.

    The Admin AI tool calls ``create_proposal`` only. Human operators
    approve/reject/apply through the shell UI via ``approve``, ``reject``,
    ``apply``.
    """

    def create_proposal(
        self,
        scope: str,
        target_path: str,
        proposed_content: str,
        description: str,
        created_by=None,
    ) -> UIStyleChangeRequest:
        """Validate and create a new style proposal atomically.

        Raises:
            ValueError: scope/path disagreement or other validation failure
        """
        from cauldron_django_admin.override_store import (
            OverrideStoreError, MAX_FILE_BYTES,
        )

        # Validate scope
        if scope not in ("admin", "pages"):
            raise ValueError(f"Invalid scope {scope!r}. Must be 'admin' or 'pages'.")

        # Reject absolute paths early.
        if target_path.startswith("/"):
            raise ValueError(
                f"target_path must be a relative path. Got: {target_path!r}"
            )

        # Reject paths that redundantly encode the scope (e.g. "admin/foo.css"
        # under scope="admin") or point at the other scope directory.
        first_component = target_path.split("/", 1)[0] if target_path else ""
        if first_component in ("admin", "pages"):
            raise ValueError(
                f"target_path must not begin with a scope directory "
                f"({first_component!r}); pass just the path within the scope."
            )

        # Validate encoding and size before hitting the store.
        try:
            encoded = proposed_content.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as exc:
            raise ValueError("proposed_content is not valid UTF-8.") from exc

        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError(
                f"proposed_content exceeds the per-file size limit of {MAX_FILE_BYTES} bytes."
            )

        # Fail-closed: the store MUST be reachable to inspect the base state.
        try:
            store = _get_override_store()
        except RuntimeError as exc:
            raise ValueError(
                "UI override store is not configured; cannot create proposal."
            ) from exc

        try:
            store.validate_target(scope, target_path)
            state = store.inspect_state(scope, target_path)
        except OverrideStoreError as exc:
            raise ValueError(f"Invalid target path: {exc}") from exc

        base_exists = bool(state["exists"])
        base_hash = state["hash"] or "" if base_exists else ""
        proposed_hash = _sha256(proposed_content)

        with transaction.atomic():
            proposal = UIStyleChangeRequest.objects.create(
                scope=scope,
                target_path=target_path,
                proposed_content=proposed_content,
                base_exists=base_exists,
                base_hash=base_hash,
                proposed_hash=proposed_hash,
                description=description,
                created_by=created_by,
                status="proposed",
            )
            UIStyleAuditEvent.objects.create(
                change_request=proposal,
                sequence=1,
                event_type="proposed",
                actor=created_by,
                detail={"scope": scope, "target_path": target_path},
            )
        return proposal

    def approve(self, proposal: UIStyleChangeRequest, reviewed_by) -> UIStyleChangeRequest:
        with transaction.atomic():
            locked = UIStyleChangeRequest.objects.select_for_update().get(pk=proposal.pk)
            if locked.status != "proposed":
                raise ValueError(f"Cannot approve a proposal with status {locked.status!r}.")
            locked.status = "approved"
            locked.reviewed_by = reviewed_by
            locked.reviewed_at = timezone.now()
            locked.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            UIStyleAuditEvent.objects.create(
                change_request=locked,
                sequence=_next_sequence_locked(locked),
                event_type="approved",
                actor=reviewed_by,
                detail={},
            )
        return locked

    def reject(self, proposal: UIStyleChangeRequest, reviewed_by) -> UIStyleChangeRequest:
        with transaction.atomic():
            locked = UIStyleChangeRequest.objects.select_for_update().get(pk=proposal.pk)
            if locked.status != "proposed":
                raise ValueError(f"Cannot reject a proposal with status {locked.status!r}.")
            locked.status = "rejected"
            locked.reviewed_by = reviewed_by
            locked.reviewed_at = timezone.now()
            locked.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            UIStyleAuditEvent.objects.create(
                change_request=locked,
                sequence=_next_sequence_locked(locked),
                event_type="rejected",
                actor=reviewed_by,
                detail={},
            )
        return locked

    def apply(self, proposal: UIStyleChangeRequest, applied_by) -> UIStyleChangeRequest:
        """Apply an approved proposal to the filesystem.

        Four-transition protocol that keeps the database and the filesystem in
        agreement even under commit-time failures and concurrent apply attempts:

        1. **Claim with applying status.** Inside a transaction, transition
           ``approved`` → ``applying`` with a UUID lease token so concurrent
           callers can detect and lose the race cleanly.
        2. **Pre-read.** Outside any transaction, capture the old file content
           for rollback. If the pre-read disagrees with ``base_hash`` we
           transition ``applying`` → ``conflicted`` and raise.
        3. **Filesystem write.** Runs OUTSIDE any transaction. Failure branches
           each open their own atomic block to record the outcome and
           transition ``applying`` → ``conflicted``, then re-raise.
        4. **Persist applied state.** Re-lock the row, verify the lease is
           still ours, then transition ``applying`` → ``applied``. If anything
           fails, roll the filesystem back to its captured pre-image and
           transition to ``approved`` (rollback succeeded) or ``conflicted``
           (rollback failed).
        """
        from cauldron_django_admin.override_store import (
            HashConflictError, OverrideStoreError, ABSENT,
        )
        store = _get_override_store()

        # ── Phase 1: claim with applying status ───────────────────────────
        lease = str(_uuid.uuid4())
        with transaction.atomic():
            locked = UIStyleChangeRequest.objects.select_for_update().get(
                pk=proposal.pk,
            )
            if locked.status != "approved":
                raise ValueError(
                    f"Cannot apply a proposal with status {locked.status!r}.",
                )
            scope = locked.scope
            target_path = locked.target_path
            proposed_content = locked.proposed_content
            base_exists = locked.base_exists
            base_hash = locked.base_hash
            locked.status = "applying"
            locked.apply_lease = lease
            locked.save(update_fields=["status", "apply_lease"])
            UIStyleAuditEvent.objects.create(
                change_request=locked,
                sequence=_next_sequence_locked(locked),
                event_type="applying",
                actor=applied_by,
                detail={},
            )

        expected = base_hash if base_exists else ABSENT

        # ── Pre-read: capture old content for rollback ────────────────────
        # For existing targets we MUST succeed and the captured bytes MUST
        # match the persisted ``base_hash`` — otherwise the target was
        # modified concurrently and we have no known-good pre-image to restore.
        old_content: str | None = None
        if base_exists:
            try:
                old_content = store.read_file(scope, target_path)
            except FileNotFoundError:
                # base_exists=True but file is gone → definitely a conflict.
                _record_pre_read_conflict(
                    proposal.pk,
                    lease,
                    applied_by,
                    "Target file is missing but was expected to exist.",
                )
                raise HashConflictError(
                    "Target file is missing but base_exists is True.",
                )
            except Exception as exc:
                raise ValueError(
                    "Cannot read existing target for rollback capture; "
                    "refusing to apply without a known-good pre-image.",
                ) from exc
            captured_hash = _sha256(old_content)
            if captured_hash != base_hash:
                _record_pre_read_conflict(
                    proposal.pk,
                    lease,
                    applied_by,
                    "Target file was modified since the proposal was created.",
                )
                raise HashConflictError(
                    "Pre-read bytes do not match persisted base_hash; "
                    "the target was modified concurrently.",
                )

        # ── Phase 2: filesystem write ─────────────────────────────────────
        proposed_hash = _sha256(proposed_content)
        new_hash: str | None = None
        try:
            new_hash = store.write_file_atomic(
                scope, target_path, proposed_content,
                expected_hash=expected,
            )
        except HashConflictError:
            # Determine whether this is a *true* conflict (someone modified
            # the target away from BOTH our expected pre-image AND our
            # proposed content) or a benign race where another apply of the
            # same proposal already won. In the latter case we must not
            # overwrite the winner's status with "conflicted".
            current_hash: str | None
            try:
                current_bytes = store.read_file(scope, target_path)
                current_hash = _sha256(current_bytes)
            except FileNotFoundError:
                current_hash = None
            except Exception:
                current_hash = None

            if current_hash == proposed_hash:
                # A concurrent apply wrote exactly the content we wanted.
                # Leave the DB state alone (winner's Phase 3 will commit
                # "applied") and re-raise so this thread's caller sees the
                # race, but without corrupting the winner's status.
                raise

            with transaction.atomic():
                locked2 = UIStyleChangeRequest.objects.select_for_update().get(
                    pk=proposal.pk,
                )
                if locked2.status == "applying" and locked2.apply_lease == lease:
                    locked2.status = "conflicted"
                    locked2.apply_lease = ""
                    locked2.error_code = "HASH_CONFLICT"
                    locked2.error_summary = (
                        "Target file was modified since the proposal was created."
                    )
                    locked2.save(update_fields=[
                        "status", "apply_lease", "error_code", "error_summary",
                    ])
                    UIStyleAuditEvent.objects.create(
                        change_request=locked2,
                        sequence=_next_sequence_locked(locked2),
                        event_type="conflict",
                        actor=applied_by,
                        detail={"error": "hash_conflict"},
                    )
            raise
        except OverrideStoreError as exc:
            with transaction.atomic():
                locked2 = UIStyleChangeRequest.objects.select_for_update().get(
                    pk=proposal.pk,
                )
                if locked2.status == "applying" and locked2.apply_lease == lease:
                    locked2.status = "conflicted"
                    locked2.apply_lease = ""
                    locked2.error_code = "STORE_ERROR"
                    locked2.error_summary = type(exc).__name__
                    locked2.save(update_fields=[
                        "status", "apply_lease", "error_code", "error_summary",
                    ])
                    UIStyleAuditEvent.objects.create(
                        change_request=locked2,
                        sequence=_next_sequence_locked(locked2),
                        event_type="failed",
                        actor=applied_by,
                        detail={"error_class": type(exc).__name__},
                    )
            raise

        # ── Phase 3: persist applied state ────────────────────────────────
        # If ANYTHING here fails (model save, audit creation, or the commit
        # itself), roll the filesystem back to its captured pre-image AND
        # persist a durable record of the rollback outcome — the DB and the
        # filesystem must never be silently inconsistent.
        try:
            with transaction.atomic():
                locked2 = UIStyleChangeRequest.objects.select_for_update().get(
                    pk=proposal.pk,
                )
                # Verify we still hold the lease — a concurrent actor may have
                # transitioned us out of 'applying' between our FS write and now.
                if locked2.status != "applying" or locked2.apply_lease != lease:
                    raise ValueError(
                        f"Lost applying lease (expected {lease!r}) — "
                        f"proposal is in status {locked2.status!r}."
                    )
                locked2.status = "applied"
                locked2.apply_lease = ""
                locked2.applied_at = timezone.now()
                locked2.proposed_hash = new_hash
                locked2.save(update_fields=[
                    "status", "apply_lease", "applied_at", "proposed_hash",
                ])
                UIStyleAuditEvent.objects.create(
                    change_request=locked2,
                    sequence=_next_sequence_locked(locked2),
                    event_type="applied",
                    actor=applied_by,
                    detail={"new_hash": new_hash},
                )
        except Exception as phase3_exc:
            rollback = _attempt_filesystem_rollback(
                store, scope, target_path, old_content, new_hash,
            )

            # Persist the rollback outcome in an INDEPENDENT transaction. If
            # rollback succeeded the proposal returns to ``approved`` for retry
            # (with an error trail so operators know Phase 3 failed once).
            # If rollback failed, mark ``conflicted`` so the row cannot be
            # applied again without human review of the DB↔FS mismatch.
            try:
                with transaction.atomic():
                    locked3 = (
                        UIStyleChangeRequest.objects
                        .select_for_update()
                        .get(pk=proposal.pk)
                    )
                    if locked3.status == "applying" and locked3.apply_lease == lease:
                        if rollback.success:
                            locked3.status = "approved"
                            locked3.apply_lease = ""
                            locked3.error_code = "APPLY_DB_FAILED"
                            locked3.error_summary = (
                                "Database write failed after filesystem write; "
                                "filesystem was restored."
                            )
                            locked3.save(update_fields=[
                                "status", "apply_lease", "error_code", "error_summary",
                            ])
                            UIStyleAuditEvent.objects.create(
                                change_request=locked3,
                                sequence=_next_sequence_locked(locked3),
                                event_type="failed",
                                actor=applied_by,
                                detail={
                                    "error_class": "DB_COMMIT_FAILED",
                                    "rollback": "succeeded",
                                },
                            )
                        else:
                            locked3.status = "conflicted"
                            locked3.apply_lease = ""
                            locked3.error_code = "ROLLBACK_FAILED"
                            locked3.error_summary = (
                                "Database and filesystem are inconsistent; "
                                "manual review required."
                            )
                            locked3.save(update_fields=[
                                "status", "apply_lease", "error_code", "error_summary",
                            ])
                            UIStyleAuditEvent.objects.create(
                                change_request=locked3,
                                sequence=_next_sequence_locked(locked3),
                                event_type="failed",
                                actor=applied_by,
                                detail={
                                    "error_class": "ROLLBACK_FAILED",
                                    "rollback_error_class": rollback.error_class,
                                },
                            )
            except Exception:
                logger.exception(
                    "CRITICAL: could not persist rollback outcome for "
                    "proposal %s",
                    proposal.pk,
                )
            raise phase3_exc

        return UIStyleChangeRequest.objects.get(pk=proposal.pk)


_service = UIStyleChangeService()


def get_style_service() -> UIStyleChangeService:
    return _service
