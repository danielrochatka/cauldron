"""FlatFile implementation of the :class:`ReversibleMutationAdapter` protocol.

Snapshots canonical files before mutation, records post-application state so
that later rollbacks can detect concurrent changes, and restores the pre-
application state on rollback.

Security invariants:
    * All paths reconstructed from rollback artifacts are resolved via
      ``safe_resolve`` against ``content_root``. Absolute paths, ``..``
      traversal, and escaping symlinks are refused.
    * A tampered ``rollback_artifact.json`` cannot cause reads or writes
      outside ``content_root``.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .paths import PathEscapeError, safe_resolve
from .store import _atomic_write_json, _read_json


class RollbackConflict(Exception):
    """Raised when the current on-disk content diverges from the recorded
    post-application state and ``force`` was not supplied."""


class RollbackNotSupported(Exception):
    """Raised when there is no rollback artifact for a given changeset."""


class RollbackPostStateUnavailable(Exception):
    """Raised when non-forced rollback lacks a valid post-application state file.

    Callers may treat this as a hard error surfaced with
    ``rollback.post_state_unavailable``.
    """


class VerificationResult:
    """Result of an adapter-level state verification call.

    Statuses:
      * ``"verified"`` — on-disk state matches recorded state
      * ``"missing_evidence"`` — no artifact was found to verify against
      * ``"mismatch"`` — a real content divergence exists
      * ``"corrupt_evidence"`` — the artifact exists but is unreadable/malformed
      * ``"unsupported"`` — verification is not available for this cs_id
    """

    __slots__ = ("status", "reason", "details")

    def __init__(self, status: str, reason: str = "", details: dict | None = None) -> None:
        self.status = status
        self.reason = reason
        self.details = dict(details or {})

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "details": dict(self.details)}


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
        # Retained for backward compatibility with earlier persisted artifacts.
        return self._snap_dir(cs_id) / "post_application_hashes.json"

    def _post_state_path(self, cs_id: str) -> Path:
        return self._snap_dir(cs_id) / "post_application_state.json"

    def _rollback_result_path(self, cs_id: str) -> Path:
        return self._snap_dir(cs_id) / "rollback_result.json"

    def _safe_resolve_content(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` inside content_root or raise.

        Refuses absolute paths, ``..`` escapes, and symlinks whose target
        escapes ``content_root``.
        """
        if rel_path is None or rel_path == "":
            raise PathEscapeError("Empty rel_path in rollback artifact.")
        if Path(rel_path).is_absolute():
            raise PathEscapeError(f"Absolute rel_path not allowed: {rel_path!r}")
        return safe_resolve(self._content_root, rel_path)

    def _rel_path_for(self, canonical: Path) -> str:
        rel = canonical.resolve().relative_to(self._content_root)
        return str(rel)

    def prepare(self, cs_id: str, changeset: Any) -> None:
        """Snapshot canonical files before mutation.

        Records the relative-to-content_root path for each operation, so that
        rollback cannot be redirected outside ``content_root`` by tampering.

        Raises if any operation cannot be resolved to a canonical path —
        every operation must produce exactly one rollback entry.
        """
        snap_dir = self._snap_dir(cs_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict] = []
        for i, op in enumerate(changeset.operations):
            canonical = self._canonical_path_for_op(op)
            if canonical is None:
                raise RuntimeError(
                    f"Cannot determine canonical path for operation index {i} "
                    f"(collection={op.collection!r}, item_id={op.item_id!r})."
                )
            # Ensure canonical is inside content_root before recording.
            try:
                rel = self._rel_path_for(canonical)
            except Exception as exc:
                raise PathEscapeError(
                    f"Canonical path escapes content_root: {canonical}"
                ) from exc
            snap_name = f"{i:04d}_{canonical.name}"
            kind_value = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            existed = canonical.exists()
            entry = {
                "op_index": i,
                "snap_name": snap_name,
                "rel_path": rel,
                "canonical_path": str(canonical),  # informational only
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
        """Record post-application state ordered by operation index.

        Also writes the legacy ``post_application_hashes.json`` map for
        backward compatibility with earlier callers.

        Raises if a create/update target does not exist after apply — that
        would be a contradiction with a "successful" application.
        """
        artifact = _read_json(self._art_path(cs_id))
        files = artifact.get("files", [])
        records: list[dict] = []
        legacy_hashes: dict[str, str] = {}
        for entry in files:
            rel_path = entry.get("rel_path")
            if rel_path is None:
                # Backward compat: older artifacts stored only canonical_path.
                canonical_str = entry.get("canonical_path", "")
                if not canonical_str:
                    raise RollbackPostStateUnavailable(
                        f"Rollback artifact for {cs_id!r} is missing rel_path."
                    )
                try:
                    rel_path = self._rel_path_for(Path(canonical_str))
                except Exception as exc:
                    raise PathEscapeError(
                        f"Legacy canonical_path escapes content_root: {canonical_str}"
                    ) from exc
            canonical = self._safe_resolve_content(rel_path)
            kind = entry.get("kind", "")
            item_id = entry.get("item_id", "")
            collection = entry.get("collection", "")
            op_index = entry.get("op_index", 0)
            file_present = canonical.exists()
            if kind in ("create", "update"):
                if not file_present:
                    raise RuntimeError(
                        f"Post-application contradiction: {kind!r} target "
                        f"{rel_path!r} does not exist on disk."
                    )
                sha = self._file_hash(canonical)
                records.append({
                    "op_index": op_index,
                    "collection": collection,
                    "item_id": item_id,
                    "rel_path": rel_path,
                    "kind": kind,
                    "expected_present": True,
                    "sha256": sha,
                })
                legacy_hashes[item_id] = sha
            elif kind == "delete":
                records.append({
                    "op_index": op_index,
                    "collection": collection,
                    "item_id": item_id,
                    "rel_path": rel_path,
                    "kind": kind,
                    "expected_present": False,
                    "sha256": "",
                })
                legacy_hashes[item_id] = ""
            else:
                # Unknown kind: treat as informational only.
                records.append({
                    "op_index": op_index,
                    "collection": collection,
                    "item_id": item_id,
                    "rel_path": rel_path,
                    "kind": kind,
                    "expected_present": file_present,
                    "sha256": self._file_hash(canonical) if file_present else "",
                })
        _atomic_write_json(self._post_state_path(cs_id), {"records": records})
        _atomic_write_json(self._post_hashes_path(cs_id), legacy_hashes)

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
        files = artifact.get("files", [])

        # For non-forced rollback we require a matching post-application state.
        # We index records by op_index for exact match to file entries.
        state_by_index: dict[int, dict] = {}
        if not force:
            state_path = self._post_state_path(cs_id)
            if not state_path.exists():
                raise RollbackPostStateUnavailable(
                    f"Post-application state missing for {cs_id!r}; "
                    "unable to safely roll back without force."
                )
            try:
                state_doc = _read_json(state_path)
                records = state_doc.get("records") or []
            except Exception as exc:
                raise RollbackPostStateUnavailable(
                    f"Post-application state for {cs_id!r} is unreadable: {exc}"
                ) from exc
            for r in records:
                if "op_index" in r:
                    state_by_index[int(r["op_index"])] = r
            if len(state_by_index) < len(files):
                raise RollbackPostStateUnavailable(
                    f"Post-application state for {cs_id!r} is incomplete: "
                    f"expected {len(files)} records, got {len(state_by_index)}."
                )

        snap_dir = self._snap_dir(cs_id)
        for entry in files:
            op_index = entry.get("op_index")
            rel_path = entry.get("rel_path")
            snap_name = entry.get("snap_name", "")
            kind = entry.get("kind", "")

            if rel_path is None:
                canonical_str = entry.get("canonical_path", "")
                if not canonical_str:
                    raise RollbackPostStateUnavailable(
                        f"Rollback entry for {cs_id!r} missing rel_path."
                    )
                try:
                    rel_path = self._rel_path_for(Path(canonical_str))
                except Exception as exc:
                    raise PathEscapeError(
                        f"Rollback canonical_path escapes content_root: {canonical_str}"
                    ) from exc

            canonical = self._safe_resolve_content(rel_path)

            if not force:
                record = state_by_index.get(int(op_index)) if op_index is not None else None
                if record is None:
                    raise RollbackPostStateUnavailable(
                        f"No post-application record for op_index {op_index} in {cs_id!r}."
                    )
                expected_present = bool(record.get("expected_present"))
                expected_sha = record.get("sha256", "")
                file_exists_now = canonical.exists()
                if not expected_present:
                    if file_exists_now:
                        raise RollbackConflict(
                            f"File {canonical.name!r} was deleted by the changeset but has "
                            "been recreated since. Use force=True to overwrite."
                        )
                else:
                    if not file_exists_now:
                        raise RollbackConflict(
                            f"File {canonical.name!r} was deleted after application. "
                            "Use force=True to overwrite."
                        )
                    current_hash = self._file_hash(canonical)
                    if current_hash != expected_sha:
                        raise RollbackConflict(
                            f"Content at {canonical.name!r} changed after application "
                            f"(post-apply hash: {expected_sha[:8]}..., "
                            f"current: {current_hash[:8]}...). "
                            "Use force=True to overwrite."
                        )

            if entry.get("existed"):
                backed_up = snap_dir / snap_name
                if backed_up.exists():
                    canonical.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backed_up, canonical)
                elif canonical.exists() and kind == "create":
                    canonical.unlink()
            else:
                if canonical.exists():
                    canonical.unlink()

        self.record_rolled_back(cs_id)

    def verify_applied_state(self, cs_id: str) -> VerificationResult:
        """Verify that on-disk state matches the recorded post-application state.

        Uses the provider-owned ``post_application_state.json`` record set so
        the check operates in the raw-file hash domain (not the semantic
        ContentItem domain).
        """
        state_path = self._post_state_path(cs_id)
        if not state_path.exists():
            return VerificationResult(
                status="missing_evidence",
                reason=f"No post_application_state.json for {cs_id!r}",
            )
        try:
            state_doc = _read_json(state_path)
            records = state_doc.get("records") or []
        except Exception as exc:
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Cannot read post_application_state.json: {exc}",
            )
        details: dict = {"checked": len(records), "issues": []}
        for r in records:
            rel = r.get("rel_path", "")
            try:
                canonical = self._safe_resolve_content(rel)
            except Exception as exc:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path {rel!r} is unsafe: {exc}",
                    details=details,
                )
            expected_present = bool(r.get("expected_present"))
            expected_sha = r.get("sha256", "")
            if expected_present:
                if not canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "missing"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Expected file present but missing: {rel!r}",
                        details=details,
                    )
                actual = self._file_hash(canonical)
                if actual != expected_sha:
                    details["issues"].append({"rel_path": rel, "reason": "hash_mismatch"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Hash mismatch at {rel!r}",
                        details=details,
                    )
            else:
                if canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "unexpected_present"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Expected file absent but present: {rel!r}",
                        details=details,
                    )
        return VerificationResult(status="verified", details=details)

    def verify_rolled_back_state(self, cs_id: str) -> VerificationResult:
        """Verify that on-disk state matches the recorded pre-application state.

        Files whose pre-state was "did not exist" must be absent after rollback.
        Files whose pre-state was "existed" must match ``pre_hash`` after rollback.
        """
        art_path = self._art_path(cs_id)
        if not art_path.exists():
            return VerificationResult(
                status="missing_evidence",
                reason=f"No rollback_artifact.json for {cs_id!r}",
            )
        try:
            artifact = _read_json(art_path)
            files = artifact.get("files", [])
        except Exception as exc:
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Cannot read rollback_artifact.json: {exc}",
            )
        details: dict = {"checked": len(files), "issues": []}
        for entry in files:
            rel = entry.get("rel_path")
            if rel is None:
                canonical_str = entry.get("canonical_path", "")
                if not canonical_str:
                    return VerificationResult(
                        status="corrupt_evidence",
                        reason="artifact entry missing rel_path",
                        details=details,
                    )
                try:
                    rel = self._rel_path_for(Path(canonical_str))
                except Exception as exc:
                    return VerificationResult(
                        status="corrupt_evidence",
                        reason=f"canonical_path unsafe: {exc}",
                        details=details,
                    )
            try:
                canonical = self._safe_resolve_content(rel)
            except Exception as exc:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path unsafe: {exc}",
                    details=details,
                )
            existed = bool(entry.get("existed"))
            pre_hash = entry.get("pre_hash", "")
            if not existed:
                if canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "unexpected_present"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"File {rel!r} should be absent after rollback",
                        details=details,
                    )
            else:
                if not canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "missing"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"File {rel!r} should be present after rollback",
                        details=details,
                    )
                actual = self._file_hash(canonical)
                if actual != pre_hash:
                    details["issues"].append({"rel_path": rel, "reason": "hash_mismatch"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Hash mismatch at {rel!r} after rollback",
                        details=details,
                    )
        return VerificationResult(status="verified", details=details)

    def has_application_result(self, cs_id: str) -> bool:
        return self._post_state_path(cs_id).exists() or self._post_hashes_path(cs_id).exists()

    def has_rollback_artifact(self, cs_id: str) -> bool:
        return self._art_path(cs_id).exists()

    def get_post_application_hashes(self, cs_id: str) -> dict[str, str]:
        """Return the legacy item_id → sha256 map.

        Prefers the newer per-op state file when available.
        """
        state_path = self._post_state_path(cs_id)
        if state_path.exists():
            try:
                state_doc = _read_json(state_path)
                return {
                    r.get("item_id", ""): r.get("sha256", "")
                    for r in state_doc.get("records") or []
                    if r.get("item_id") is not None
                }
            except Exception:
                pass
        ph_path = self._post_hashes_path(cs_id)
        if not ph_path.exists():
            return {}
        try:
            return _read_json(ph_path)
        except Exception:
            return {}

    def inspect(self, cs_id: str) -> dict:
        info: dict = {
            "cs_id": cs_id,
            "has_rollback_artifact": self.has_rollback_artifact(cs_id),
            "has_application_result": self.has_application_result(cs_id),
            "has_rollback_result": self._rollback_result_path(cs_id).exists(),
        }
        try:
            info["applied_state"] = self.verify_applied_state(cs_id).to_dict()
        except Exception as exc:
            info["applied_state"] = {"status": "corrupt_evidence", "reason": str(exc)[:200]}
        try:
            info["rolled_back_state"] = self.verify_rolled_back_state(cs_id).to_dict()
        except Exception as exc:
            info["rolled_back_state"] = {"status": "corrupt_evidence", "reason": str(exc)[:200]}
        return info

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
