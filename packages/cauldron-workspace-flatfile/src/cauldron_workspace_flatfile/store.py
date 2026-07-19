"""Persistent storage for content change-sets."""
from __future__ import annotations

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


class ChangeSetState(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


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
