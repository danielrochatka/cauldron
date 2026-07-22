"""FlatFile implementation of the :class:`ReversibleMutationAdapter` protocol.

Snapshots canonical files before mutation, records post-application hashes so
that later rollbacks can detect concurrent changes, and restores the pre-
application state on rollback.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .paths import safe_resolve
from .store import _atomic_write_json, _read_json


class RollbackConflict(Exception):
    """Raised when the current on-disk content diverges from the recorded
    post-application hashes and ``force`` was not supplied."""


class RollbackNotSupported(Exception):
    """Raised when there is no rollback artifact for a given changeset."""


class FlatFileReversibleMutationAdapter:
    """Reversible mutation adapter for the flatfile CMS provider."""

    def __init__(self, config: WorkspaceConfig, content_root: Path) -> None:
        self._config = config
        self._content_root = Path(content_root).resolve()

    # ------------------------------------------------------------------
    # Protocol properties/methods
    # ------------------------------------------------------------------

    @property
    def supports_rollback(self) -> bool:
        return True

    def _snap_dir(self, cs_id: str) -> Path:
        return safe_resolve(self._config.snapshots_dir, cs_id)

    def _art_path(self, cs_id: str) -> Path:
        return self._snap_dir(cs_id) / "rollback_artifact.json"

    def _post_hashes_path(self, cs_id: str) -> Path:
        return self._snap_dir(cs_id) / "post_application_hashes.json"

    def _rollback_result_path(self, cs_id: str) -> Path:
        return self._snap_dir(cs_id) / "rollback_result.json"

    def prepare(self, cs_id: str, changeset: Any) -> None:
        """Snapshot canonical files before mutation."""
        snap_dir = self._snap_dir(cs_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict] = []
        for i, op in enumerate(changeset.operations):
            canonical = self._canonical_path_for_op(op)
            if canonical is None:
                continue
            snap_name = f"{i:04d}_{canonical.name}"
            kind_value = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            existed = canonical.exists()
            entry = {
                "snap_name": snap_name,
                "canonical_path": str(canonical),
                "collection": op.collection,
                "item_id": op.item_id,
                "kind": kind_value,
                "existed": existed,
                "pre_hash": self._file_hash(canonical) if existed else "",
            }
            if existed:
                shutil.copy2(canonical, snap_dir / snap_name)
            files.append(entry)
        _atomic_write_json(self._art_path(cs_id), {"cs_id": cs_id, "files": files})

    def record_applied(self, cs_id: str) -> None:
        """Scan canonical files from the rollback artifact and store raw SHA-256 hashes.

        Must be called after mutation. Delete operations are recorded as empty string
        (expected absent). This ensures rollback conflict detection operates in the
        same hash domain as the rollback itself.
        """
        artifact = _read_json(self._art_path(cs_id))
        post_hashes: dict[str, str] = {}
        for entry in artifact.get("files", []):
            item_id = entry.get("item_id", "")
            canonical = Path(entry["canonical_path"])
            kind = entry.get("kind", "")
            if kind == "delete" or not canonical.exists():
                post_hashes[item_id] = ""
            else:
                post_hashes[item_id] = self._file_hash(canonical)
        _atomic_write_json(self._post_hashes_path(cs_id), post_hashes)

    def record_rolled_back(self, cs_id: str) -> None:
        _atomic_write_json(self._rollback_result_path(cs_id), {"rolled_back": True})

    def rollback(
        self,
        cs_id: str,
        *,
        force: bool = False,
        is_superuser: bool = False,
    ) -> None:
        art_path = self._art_path(cs_id)
        if not art_path.exists():
            raise RollbackNotSupported(
                f"No rollback artifact for changeset {cs_id!r}"
            )
        if force and not is_superuser:
            raise PermissionError("Forced rollback requires superuser privileges.")

        artifact = _read_json(art_path)
        post_hashes: dict[str, str] = {}
        ph_path = self._post_hashes_path(cs_id)
        if ph_path.exists():
            try:
                post_hashes = _read_json(ph_path)
            except Exception:
                post_hashes = {}

        snap_dir = self._snap_dir(cs_id)
        for entry in artifact.get("files", []):
            canonical = Path(entry["canonical_path"])
            snap_name = entry["snap_name"]
            kind = entry.get("kind", "")

            if not force and post_hashes:
                item_id = entry.get("item_id", "")
                post_hash = post_hashes.get(item_id, "")
                file_exists_now = canonical.exists()
                if post_hash == "":
                    # Expected absent (was a delete operation).
                    if file_exists_now:
                        raise RollbackConflict(
                            f"File {canonical.name!r} was deleted by the changeset but has "
                            "been recreated since. Use force=True to overwrite."
                        )
                else:
                    # Expected present.
                    if not file_exists_now:
                        raise RollbackConflict(
                            f"File {canonical.name!r} was deleted after application. "
                            "Use force=True to overwrite."
                        )
                    current_hash = self._file_hash(canonical)
                    if current_hash != post_hash:
                        raise RollbackConflict(
                            f"Content at {canonical.name!r} changed after application "
                            f"(post-apply hash: {post_hash[:8]}..., current: {current_hash[:8]}...). "
                            "Use force=True to overwrite."
                        )

            if entry.get("existed"):
                backed_up = snap_dir / snap_name
                if backed_up.exists():
                    shutil.copy2(backed_up, canonical)
                elif canonical.exists() and kind == "create":
                    canonical.unlink()
            else:
                if canonical.exists():
                    canonical.unlink()

        self.record_rolled_back(cs_id)

    def has_application_result(self, cs_id: str) -> bool:
        return self._post_hashes_path(cs_id).exists()

    def has_rollback_artifact(self, cs_id: str) -> bool:
        return self._art_path(cs_id).exists()

    def get_post_application_hashes(self, cs_id: str) -> dict[str, str]:
        ph_path = self._post_hashes_path(cs_id)
        if not ph_path.exists():
            return {}
        try:
            return _read_json(ph_path)
        except Exception:
            return {}

    def inspect(self, cs_id: str) -> dict:
        return {
            "cs_id": cs_id,
            "has_rollback_artifact": self.has_rollback_artifact(cs_id),
            "has_application_result": self.has_application_result(cs_id),
            "has_rollback_result": self._rollback_result_path(cs_id).exists(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _canonical_path_for_op(self, op: Any) -> Path | None:
        """Best-effort resolution of the on-disk path for an operation."""
        try:
            from cauldron_content.contracts import ContentOperationKind
        except Exception:  # pragma: no cover - contract package must be available
            ContentOperationKind = None  # type: ignore[assignment]

        coll_dir = self._content_root / op.collection
        kind = op.kind
        if ContentOperationKind is not None and isinstance(kind, ContentOperationKind):
            kind_value = kind.value
        else:
            kind_value = str(kind)

        if kind_value == "create":
            slug = op.slug or op.item_id
            if not slug:
                return None
            return coll_dir / f"{slug}.md"
        if kind_value in ("update", "delete"):
            existing = self._find_file_for_item(op.collection, op.item_id)
            if existing is not None:
                return existing
            slug = op.slug or op.item_id
            if not slug:
                return None
            return coll_dir / f"{slug}.md"
        return None

    def _find_file_for_item(self, collection: str, item_id: str) -> Path | None:
        coll_dir = self._content_root / collection
        if not coll_dir.exists():
            return None
        try:
            import yaml  # type: ignore
        except Exception:
            return None
        try:
            for f in coll_dir.glob("*.md"):
                text = f.read_text(encoding="utf-8")
                if text.startswith("---"):
                    try:
                        end = text.index("---", 3)
                    except ValueError:
                        continue
                    try:
                        front = yaml.safe_load(text[3:end])
                    except Exception:
                        continue
                    if isinstance(front, dict) and front.get("id") == item_id:
                        return f
        except Exception:
            return None
        return None

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
