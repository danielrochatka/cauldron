"""Snapshot service for capturing canonical files before applying change-sets."""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import WorkspaceConfig
from .paths import safe_resolve
from .store import _atomic_write_json, _read_json


class SnapshotConflict(Exception):
    """Raised when a canonical file has changed since a snapshot was taken."""


class SnapshotService:
    def __init__(self, config: WorkspaceConfig) -> None:
        self._config = config

    def capture(self, cs_id: str, canonical_paths: list[Path]) -> None:
        """Copy canonical files into ``snapshots/<cs_id>``.

        Non-existent files are recorded with ``existed=False`` so that rollback
        can delete them (i.e. undo a create).
        """
        snap_dir = safe_resolve(self._config.snapshots_dir, cs_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"cs_id": cs_id, "files": []}
        for src_path in canonical_paths:
            entry = {
                "name": src_path.name,
                "original_path": str(src_path),
                "existed": src_path.exists(),
            }
            if entry["existed"]:
                dest = snap_dir / src_path.name
                shutil.copy2(src_path, dest)
            manifest["files"].append(entry)
        _atomic_write_json(snap_dir / "snapshot.json", manifest)

    def rollback(self, cs_id: str, *, force: bool = False) -> None:
        """Restore canonical files from the snapshot.

        Raises :class:`SnapshotConflict` if a canonical file changed since the
        snapshot was taken and ``force`` is False.
        """
        snap_dir = safe_resolve(self._config.snapshots_dir, cs_id)
        manifest = _read_json(snap_dir / "snapshot.json")
        for entry in manifest["files"]:
            canonical = Path(entry["original_path"])
            if entry["existed"]:
                backed_up = snap_dir / entry["name"]
                if canonical.exists() and not force:
                    current_data = canonical.read_bytes()
                    backed_data = backed_up.read_bytes()
                    if current_data != backed_data:
                        raise SnapshotConflict(
                            f"Canonical file {canonical} changed since snapshot was taken."
                        )
                shutil.copy2(backed_up, canonical)
            else:
                if canonical.exists():
                    canonical.unlink()
