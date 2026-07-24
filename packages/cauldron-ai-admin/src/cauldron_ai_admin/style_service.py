"""UIStyleChangeService — lifecycle logic for AI style proposals."""
from __future__ import annotations

import hashlib
import logging
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


def _attempt_filesystem_rollback(store, locked, old_content, written_hash):
    """Best-effort filesystem restore after a post-write DB failure.

    If we had captured the previous file content we try to restore it using
    the just-written hash as the optimistic-lock witness. If there was no
    previous content (the file was created by this apply), we try to delete
    the freshly-written file instead. Either way, we swallow any error and
    log a loud message so operators can reconcile manually.
    """
    from cauldron_django_admin.override_store import ABSENT

    try:
        if old_content is not None:
            store.write_file_atomic(
                locked.scope, locked.target_path, old_content,
                expected_hash=written_hash,
            )
        else:
            store.delete_file_atomic(
                locked.scope, locked.target_path, expected_hash=written_hash,
            )
    except Exception:
        logger.exception(
            "CRITICAL: filesystem write succeeded but DB failed and rollback "
            "also failed. Manual reconciliation required for scope=%r target=%r",
            locked.scope, locked.target_path,
        )


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
        """Apply an approved proposal to the filesystem atomically.

        The row is locked with ``select_for_update`` inside an outer atomic
        block. The filesystem write happens after the status/base checks —
        on filesystem failure the outer block is committed with the failure
        recorded (conflict/store-error) so the audit trail survives even
        though we re-raise. On post-write DB failure we make a best-effort
        attempt to roll the filesystem back to its previous state.
        """
        from cauldron_django_admin.override_store import (
            HashConflictError, OverrideStoreError, ABSENT,
        )
        store = _get_override_store()

        # Outer atomic: ensures the applied status + audit event commit
        # together, and (for the conflict/failure branches) that the audit
        # event and the recorded error status commit together even though
        # we re-raise past the caller.
        fs_error: Exception | None = None
        with transaction.atomic():
            locked = UIStyleChangeRequest.objects.select_for_update().get(pk=proposal.pk)
            if locked.status != "approved":
                raise ValueError(f"Cannot apply a proposal with status {locked.status!r}.")

            # Use the persisted base state from the model — never a fresh
            # filesystem read here, otherwise a mid-proposal modification
            # would be silently swallowed instead of surfaced as a conflict.
            expected = locked.base_hash if locked.base_exists else ABSENT

            # Capture the previous file content (if any) so we can roll the
            # filesystem back if the DB commit fails after the write.
            old_content = None
            if locked.base_exists:
                try:
                    old_content = store.read_file(locked.scope, locked.target_path)
                except Exception:
                    old_content = None

            # Filesystem write. Use a savepoint so any DB writes in the
            # except-branches commit with the outer transaction, not roll
            # back with the raised exception.
            try:
                new_hash = store.write_file_atomic(
                    locked.scope, locked.target_path, locked.proposed_content,
                    expected_hash=expected,
                )
            except HashConflictError as exc:
                locked.status = "conflicted"
                locked.error_code = "HASH_CONFLICT"
                locked.error_summary = (
                    "Target file was modified since the proposal was created."
                )
                locked.save(update_fields=[
                    "status", "error_code", "error_summary",
                ])
                UIStyleAuditEvent.objects.create(
                    change_request=locked,
                    sequence=_next_sequence_locked(locked),
                    event_type="conflict",
                    actor=applied_by,
                    detail={"error": "hash_conflict"},
                )
                fs_error = exc
            except OverrideStoreError as exc:
                locked.error_code = "STORE_ERROR"
                locked.error_summary = type(exc).__name__
                locked.save(update_fields=["error_code", "error_summary"])
                UIStyleAuditEvent.objects.create(
                    change_request=locked,
                    sequence=_next_sequence_locked(locked),
                    event_type="failed",
                    actor=applied_by,
                    detail={"error_class": type(exc).__name__},
                )
                fs_error = exc
            else:
                # Filesystem write succeeded; persist the applied state.
                try:
                    locked.status = "applied"
                    locked.applied_at = timezone.now()
                    locked.proposed_hash = new_hash
                    locked.save(update_fields=[
                        "status", "applied_at", "proposed_hash",
                    ])
                    UIStyleAuditEvent.objects.create(
                        change_request=locked,
                        sequence=_next_sequence_locked(locked),
                        event_type="applied",
                        actor=applied_by,
                        detail={"new_hash": new_hash},
                    )
                except Exception:
                    # DB failed after filesystem write — attempt to roll the
                    # filesystem back to its previous state before re-raising.
                    _attempt_filesystem_rollback(
                        store, locked, old_content, new_hash,
                    )
                    raise

        # Re-raise filesystem failures AFTER the outer atomic block has
        # committed so the conflict/failure state remains visible.
        if fs_error is not None:
            raise fs_error
        return locked


_service = UIStyleChangeService()


def get_style_service() -> UIStyleChangeService:
    return _service
