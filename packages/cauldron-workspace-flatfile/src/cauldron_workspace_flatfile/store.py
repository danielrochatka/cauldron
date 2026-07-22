"""Persistent storage for content change-sets."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .config import WorkspaceConfig
from .paths import safe_resolve

if TYPE_CHECKING:
    from cauldron_content.contracts import ContentChangeSet


def compute_changeset_hash(changeset: "ContentChangeSet") -> str:
    """Deterministic SHA-256 over a ContentChangeSet.

    Independent of caller-supplied dict key ordering: serializes each operation
    using a fixed field order and sorted dict keys within ``data``.
    Preserves operation order (order-sensitive: reordering ops changes the hash).
    ``force`` is always serialized as ``False`` — public proposals must not be
    able to carry force through into the payload hash.
    """
    def _sort_deep(obj):
        if isinstance(obj, dict):
            return {k: _sort_deep(v) for k, v in sorted(obj.items())}
        if isinstance(obj, list):
            return [_sort_deep(v) for v in obj]
        return obj

    ops_serialized = []
    for op in changeset.operations:
        kind = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
        status = op.status.value if hasattr(op.status, "value") else str(op.status)
        ops_serialized.append({
            "body": op.body or "",
            "collection": op.collection or "",
            "data": _sort_deep(dict(op.data or {})),
            "expected_hash": op.expected_hash or "",
            "force": False,
            "item_id": op.item_id or "",
            "kind": kind,
            "provider": op.provider or "",
            "schema": op.schema or "",
            "slug": op.slug or "",
            "status": status,
        })
    payload = {"operations": ops_serialized}
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ChangeSetState(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


# NOTE: SQL lifecycle has states without a direct workspace equivalent
# (rolling_back, applying, applied, rolled_back, rollback_failed,
#  reconciliation_required). The workspace state machine is a coarse mirror
# used for observability only; SQL is authoritative.
_VALID_TRANSITIONS: dict[ChangeSetState, set[ChangeSetState]] = {
    ChangeSetState.PROPOSED: {
        ChangeSetState.VALIDATED,
        ChangeSetState.REJECTED,
        ChangeSetState.FAILED,
    },
    ChangeSetState.VALIDATED: {
        ChangeSetState.APPLIED,
        ChangeSetState.REJECTED,
        ChangeSetState.FAILED,
    },
    ChangeSetState.APPLIED: set(),
    ChangeSetState.REJECTED: set(),
    ChangeSetState.FAILED: set(),
}


class ChangeSetStoreError(Exception):
    """Raised for invalid state transitions or persistence failures."""


class ChangeSetStore:
    def __init__(self, config: WorkspaceConfig) -> None:
        self._config = config

    def _cs_dir(self, cs_id: str) -> Path:
        return safe_resolve(self._config.change_sets_dir, cs_id)

    def create(
        self,
        changeset: "ContentChangeSet",
        *,
        state: ChangeSetState = ChangeSetState.PROPOSED,
    ) -> None:
        """Persist a new ContentChangeSet."""
        cs_dir = self._cs_dir(changeset.id)
        cs_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "id": changeset.id,
            "state": state.value,
            "author": changeset.author,
            "description": changeset.description,
        }
        _atomic_write_json(cs_dir / "manifest.json", manifest)
        payload = {
            "operations": [
                {
                    "kind": op.kind.value,
                    "provider": op.provider,
                    "collection": op.collection,
                    "item_id": op.item_id,
                    "slug": op.slug,
                    "expected_hash": op.expected_hash,
                    "data": op.data,
                    "body": op.body,
                    "schema": op.schema,
                    "status": op.status.value,
                    "force": op.force,
                }
                for op in changeset.operations
            ]
        }
        _atomic_write_json(cs_dir / "payload.json", payload)

    def get_state(self, cs_id: str) -> ChangeSetState:
        cs_dir = self._cs_dir(cs_id)
        manifest = _read_json(cs_dir / "manifest.json")
        return ChangeSetState(manifest["state"])

    def transition(self, cs_id: str, new_state: ChangeSetState) -> None:
        cs_dir = self._cs_dir(cs_id)
        manifest = _read_json(cs_dir / "manifest.json")
        current = ChangeSetState(manifest["state"])
        if new_state not in _VALID_TRANSITIONS[current]:
            raise ChangeSetStoreError(
                f"Invalid transition {current.value} -> {new_state.value} for {cs_id!r}"
            )
        manifest["state"] = new_state.value
        _atomic_write_json(cs_dir / "manifest.json", manifest)

    def list_ids(self) -> list[str]:
        cs_root = self._config.change_sets_dir
        if not cs_root.exists():
            return []
        return sorted(p.name for p in cs_root.iterdir() if p.is_dir())

    def save_result(self, cs_id: str, result_data: dict) -> None:
        cs_dir = self._cs_dir(cs_id)
        _atomic_write_json(cs_dir / "result.json", result_data)

    # ---------------------------------------------------------------
    # Public access helpers (avoid consumers reading private state).
    # ---------------------------------------------------------------

    @property
    def locks_dir(self) -> Path:
        """Directory to place file-based locks."""
        return self._config.locks_dir

    def load_changeset(self, cs_id: str) -> "ContentChangeSet":
        """Load a ContentChangeSet from the workspace.

        Raises :class:`ChangeSetStoreError` if the changeset payload is missing
        or cannot be parsed.
        """
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
            ContentStatus,
        )
        cs_dir = self._cs_dir(cs_id)
        payload_path = cs_dir / "payload.json"
        if not payload_path.exists():
            raise ChangeSetStoreError(f"No payload found for changeset {cs_id!r}")
        try:
            data = _read_json(payload_path)
        except Exception as exc:  # pragma: no cover - defensive
            raise ChangeSetStoreError(
                f"Failed to read payload for changeset {cs_id!r}: {exc}"
            ) from exc
        manifest_path = cs_dir / "manifest.json"
        manifest: dict = {}
        if manifest_path.exists():
            try:
                manifest = _read_json(manifest_path)
            except Exception:
                manifest = {}
        ops = []
        for op_data in data.get("operations", []):
            try:
                kind = ContentOperationKind(op_data["kind"])
                status = ContentStatus(op_data.get("status", "draft"))
            except (KeyError, ValueError) as exc:
                raise ChangeSetStoreError(
                    f"Invalid operation in changeset {cs_id!r}: {exc}"
                ) from exc
            ops.append(
                ContentOperation(
                    kind=kind,
                    provider=op_data.get("provider", ""),
                    collection=op_data.get("collection", ""),
                    item_id=op_data.get("item_id", ""),
                    slug=op_data.get("slug", ""),
                    expected_hash=op_data.get("expected_hash", ""),
                    data=op_data.get("data", {}),
                    body=op_data.get("body", ""),
                    schema=op_data.get("schema", ""),
                    status=status,
                    force=bool(op_data.get("force", False)),
                )
            )
        return ContentChangeSet(
            id=cs_id,
            operations=tuple(ops),
            author=manifest.get("author", ""),
            description=manifest.get("description", ""),
        )

    def load_changeset_with_hash(self, cs_id: str) -> tuple["ContentChangeSet", str]:
        """Load a changeset AND compute its canonical hash from persisted state.

        Any consumer that needs to verify workspace payload integrity should use
        this method rather than trusting the caller-supplied hash.
        """
        cs = self.load_changeset(cs_id)
        return cs, compute_changeset_hash(cs)

    def cleanup_orphan(self, cs_id: str) -> None:
        """Remove a changeset directory (used for concurrent-insert cleanup).

        Only intended for the losing side of a concurrent create; callers must
        ensure they only pass a cs_id that was created by the losing request.
        """
        import shutil
        try:
            cs_dir = self._cs_dir(cs_id)
        except Exception:
            return
        if cs_dir.exists():
            try:
                shutil.rmtree(cs_dir)
            except Exception:
                # Best-effort cleanup; do not raise from cleanup.
                pass

    def save_application_result(self, cs_id: str, result: dict) -> None:
        """Record a typed application result (includes ``result_type='applied'``)."""
        cs_dir = self._cs_dir(cs_id)
        payload = {"result_type": "applied", **dict(result)}
        _atomic_write_json(cs_dir / "application_result.json", payload)

    def save_rollback_result(self, cs_id: str, result: dict) -> None:
        """Record a typed rollback result (includes ``result_type='rolled_back'``)."""
        cs_dir = self._cs_dir(cs_id)
        payload = {"result_type": "rolled_back", **dict(result)}
        _atomic_write_json(cs_dir / "rollback_result.json", payload)

    def load_application_result(self, cs_id: str) -> dict | None:
        cs_dir = self._cs_dir(cs_id)
        path = cs_dir / "application_result.json"
        if not path.exists():
            return None
        try:
            return _read_json(path)
        except Exception:
            return None

    def load_rollback_result(self, cs_id: str) -> dict | None:
        cs_dir = self._cs_dir(cs_id)
        path = cs_dir / "rollback_result.json"
        if not path.exists():
            return None
        try:
            return _read_json(path)
        except Exception:
            return None


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically using a temp file and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(tmp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
        tmp = None
    finally:
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
