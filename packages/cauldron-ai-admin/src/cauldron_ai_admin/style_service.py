"""UIStyleChangeService — lifecycle logic for AI style proposals."""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from .models import UIStyleChangeRequest, UIStyleAuditEvent

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)

_ABSENT = "__absent__"


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


def _next_sequence(proposal: UIStyleChangeRequest) -> int:
    from django.db.models import Max
    result = UIStyleAuditEvent.objects.filter(change_request=proposal).aggregate(Max("sequence"))
    return (result["sequence__max"] or 0) + 1


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
            cauldron_django_admin.override_store.OverrideStoreError: invalid path
        """
        from cauldron_django_admin.override_store import (
            UIOverrideStore, OverrideStoreError, _MAX_FILE_BYTES,
        )

        # Validate scope
        if scope not in ("admin", "pages"):
            raise ValueError(f"Invalid scope {scope!r}. Must be 'admin' or 'pages'.")

        # Reject absolute paths
        if target_path.startswith("/"):
            raise ValueError(
                f"target_path must be a relative path. Got: {target_path!r}"
            )

        # Validate encoding and size
        try:
            encoded = proposed_content.encode("utf-8")
        except (UnicodeEncodeError, AttributeError) as exc:
            raise ValueError("proposed_content is not valid UTF-8.") from exc

        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError(
                f"proposed_content exceeds the per-file size limit of {_MAX_FILE_BYTES} bytes."
            )

        # Validate path through store (checks traversal, extension, hidden, etc.)
        try:
            store = _get_override_store()
        except RuntimeError:
            store = None

        base_hash = ""
        if store is not None:
            try:
                # Validate the path (will raise on invalid path)
                store._resolve_path(scope, target_path)
                # Capture current base state
                try:
                    base_hash = store.calculate_hash(scope, target_path)
                except FileNotFoundError:
                    base_hash = _ABSENT
            except OverrideStoreError as exc:
                raise ValueError(f"Invalid target path: {exc}") from exc

        proposed_hash = _sha256(proposed_content)

        with transaction.atomic():
            proposal = UIStyleChangeRequest.objects.create(
                scope=scope,
                target_path=target_path,
                proposed_content=proposed_content,
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
                sequence=_next_sequence(locked),
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
                sequence=_next_sequence(locked),
                event_type="rejected",
                actor=reviewed_by,
                detail={},
            )
        return locked

    def apply(self, proposal: UIStyleChangeRequest, applied_by) -> UIStyleChangeRequest:
        from cauldron_django_admin.override_store import (
            UIOverrideStore, HashConflictError, OverrideStoreError, ABSENT,
        )

        # First: validate status inside a transaction
        with transaction.atomic():
            locked = UIStyleChangeRequest.objects.select_for_update().get(pk=proposal.pk)
            if locked.status != "approved":
                raise ValueError(f"Cannot apply a proposal with status {locked.status!r}.")
            locked_pk = locked.pk
            locked_scope = locked.scope
            locked_target_path = locked.target_path
            locked_proposed_content = locked.proposed_content
            locked_base_hash = locked.base_hash

        store = _get_override_store()
        expected = locked_base_hash if locked_base_hash else ABSENT

        try:
            new_hash = store.write_file_atomic(
                locked_scope,
                locked_target_path,
                locked_proposed_content,
                expected_hash=expected,
            )
        except HashConflictError:
            # Save conflict status in a new transaction
            with transaction.atomic():
                UIStyleChangeRequest.objects.filter(pk=locked_pk).update(
                    status="conflicted",
                    error_code="HASH_CONFLICT",
                    error_summary="Target file was modified since the proposal was created.",
                )
                refreshed = UIStyleChangeRequest.objects.get(pk=locked_pk)
                UIStyleAuditEvent.objects.create(
                    change_request=refreshed,
                    sequence=_next_sequence(refreshed),
                    event_type="conflict",
                    actor=applied_by,
                    detail={"error": "hash_conflict"},
                )
            raise
        except OverrideStoreError as exc:
            with transaction.atomic():
                UIStyleChangeRequest.objects.filter(pk=locked_pk).update(
                    error_code="STORE_ERROR",
                    error_summary=type(exc).__name__,
                )
                refreshed = UIStyleChangeRequest.objects.get(pk=locked_pk)
                UIStyleAuditEvent.objects.create(
                    change_request=refreshed,
                    sequence=_next_sequence(refreshed),
                    event_type="failed",
                    actor=applied_by,
                    detail={"error_class": type(exc).__name__},
                )
            raise

        # Success: update to applied
        with transaction.atomic():
            UIStyleChangeRequest.objects.filter(pk=locked_pk).update(
                status="applied",
                applied_at=timezone.now(),
                proposed_hash=new_hash,
            )
            refreshed = UIStyleChangeRequest.objects.get(pk=locked_pk)
            UIStyleAuditEvent.objects.create(
                change_request=refreshed,
                sequence=_next_sequence(refreshed),
                event_type="applied",
                actor=applied_by,
                detail={"new_hash": new_hash},
            )

        return refreshed


_service = UIStyleChangeService()


def get_style_service() -> UIStyleChangeService:
    return _service
