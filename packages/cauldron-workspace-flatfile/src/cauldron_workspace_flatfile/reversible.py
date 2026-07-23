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
    * Every rollback is preflighted end-to-end before any mutation is
      performed; a malicious later entry cannot cause partial restoration.
    * Snapshot files are content-verified against a SHA-256 digest recorded
      at ``prepare()`` time before any copy.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .paths import PathEscapeError, safe_resolve
from .store import _atomic_write_json, _read_json


ROLLBACK_ARTIFACT_VERSION = 2
POST_STATE_VERSION = 2

SUPPORTED_KINDS = frozenset({"create", "update", "delete"})


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


class RollbackArtifactInvalid(Exception):
    """Raised when the rollback artifact fails preflight validation.

    Distinct from :class:`RollbackConflict` so callers can distinguish
    "artifact tampered / malformed" from "content drifted after apply".
    """


class RollbackReconciliationRequired(Exception):
    """Raised when a rollback preflight succeeded but Phase 2 partially
    failed. The caller should not treat rollback as complete."""


class DuplicateTargetError(Exception):
    """Raised when multiple operations target the same canonical file."""


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


@dataclass(frozen=True)
class PreparationResult:
    """Typed result of :meth:`FlatFileReversibleMutationAdapter.prepare`.

    Fields:
      * ``artifact_digest`` — SHA-256 hex digest of the written
        ``rollback_artifact.json`` bytes (used as trusted evidence when
        cross-referenced from SQL metadata).
      * ``entry_count`` — total number of entries recorded in the artifact.
    """

    artifact_digest: str
    entry_count: int


class FlatFileReversibleMutationAdapter:
    """Reversible mutation adapter for the flatfile CMS provider."""

    # Item 2: declare the protocol version we implement so
    # ``ContentOperationService._adapter_fully_supports_rollback`` can enforce
    # a version match at registration and at every apply/rollback/reconcile.
    reversible_adapter_version = 2

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
        escapes ``content_root``. Also rejects path segments that look like
        embedded separators or Windows drive letters.
        """
        if rel_path is None or rel_path == "":
            raise PathEscapeError("Empty rel_path in rollback artifact.")
        p = Path(rel_path)
        if p.is_absolute():
            raise PathEscapeError(f"Absolute rel_path not allowed: {rel_path!r}")
        # Reject any component that is exactly ".." (safe_resolve also catches
        # ../../ escapes downstream, but this covers the shallow case cleanly).
        for part in p.parts:
            if part == "..":
                raise PathEscapeError(f"Traversal component not allowed: {rel_path!r}")
        return safe_resolve(self._content_root, rel_path)

    def _safe_resolve_snap(self, snap_dir: Path, snap_name: str) -> Path:
        """Resolve a snapshot filename inside ``snap_dir`` or raise.

        Snapshot names are opaque tokens like ``0000_slug.md`` — they must
        never contain path separators, absolute prefixes, or ``..``.
        """
        if not snap_name:
            raise PathEscapeError("Empty snap_name in rollback artifact.")
        p = Path(snap_name)
        if p.is_absolute() or len(p.parts) != 1:
            raise PathEscapeError(f"Unsafe snap_name: {snap_name!r}")
        if p.parts[0] in ("..", "."):
            raise PathEscapeError(f"Unsafe snap_name: {snap_name!r}")
        return safe_resolve(snap_dir, snap_name)

    def _rel_path_for(self, canonical: Path) -> str:
        rel = canonical.resolve().relative_to(self._content_root)
        return str(rel)

    def _artifact_digest(self, art_path: Path) -> str:
        """Compute the SHA-256 hex digest of a written artifact file."""
        return hashlib.sha256(art_path.read_bytes()).hexdigest()

    def prepare(self, cs_id: str, changeset: Any) -> PreparationResult:
        """Snapshot canonical files before mutation.

        Records the relative-to-content_root path for each operation, so that
        rollback cannot be redirected outside ``content_root`` by tampering.

        Raises:
          * :class:`PathEscapeError` if a canonical path escapes content_root.
          * :class:`DuplicateTargetError` if two operations target the same
            canonical relative path.
          * :class:`RuntimeError` if an operation cannot be resolved.

        Returns a :class:`PreparationResult` containing the SHA-256 digest of
        the written artifact and the recorded entry count.
        """
        snap_dir = self._snap_dir(cs_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict] = []
        seen_rels: dict[str, int] = {}
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

            # Item 9: duplicate targets within a single changeset are rejected.
            if rel in seen_rels:
                raise DuplicateTargetError(
                    f"Duplicate canonical target {rel!r} for op indexes "
                    f"{seen_rels[rel]} and {i}"
                )
            seen_rels[rel] = i

            snap_name = f"{i:04d}_{canonical.name}"
            kind_value = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            existed = canonical.exists()
            entry: dict = {
                "op_index": i,
                "snap_name": snap_name,
                "rel_path": rel,
                "collection": op.collection,
                "item_id": op.item_id,
                "kind": kind_value,
                "existed": existed,
                "pre_hash": self._file_hash(canonical) if existed else "",
                "snap_sha256": "",
            }
            if existed:
                snap_target = self._safe_resolve_snap(snap_dir, snap_name)
                shutil.copy2(canonical, snap_target)
                entry["snap_sha256"] = self._file_hash(snap_target)
            files.append(entry)

        artifact = {
            "version": ROLLBACK_ARTIFACT_VERSION,
            "cs_id": cs_id,
            "files": files,
        }
        art_path = self._art_path(cs_id)
        _atomic_write_json(art_path, artifact)
        digest = self._artifact_digest(art_path)
        return PreparationResult(artifact_digest=digest, entry_count=len(files))

    def record_applied(
        self,
        cs_id: str,
        *,
        artifact_digest: str = "",
    ) -> None:
        """Record post-application state ordered by operation index.

        Also writes the legacy ``post_application_hashes.json`` map for
        backward compatibility with earlier callers.

        Raises if a create/update target does not exist after apply — that
        would be a contradiction with a "successful" application.

        Item 8: unknown operation kinds are rejected as errors rather than
        recorded as informational entries — post-state must be authoritative.
        """
        artifact = _read_json(self._art_path(cs_id))
        files = artifact.get("files", [])
        # Compute the artifact digest so we can bind post-state to the artifact.
        computed_digest = self._artifact_digest(self._art_path(cs_id))
        if artifact_digest and artifact_digest != computed_digest:
            raise RollbackPostStateUnavailable(
                f"artifact digest mismatch for {cs_id!r}."
            )
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
                rel_path = self._legacy_rel_from_canonical(canonical_str)
            canonical = self._safe_resolve_content(rel_path)
            kind = entry.get("kind", "")
            if kind not in SUPPORTED_KINDS:
                raise RollbackPostStateUnavailable(
                    f"Unsupported op kind {kind!r} in artifact for {cs_id!r}."
                )
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
        # Post-state binds itself to the artifact via the digest.
        post_state = {
            "version": POST_STATE_VERSION,
            "cs_id": cs_id,
            "artifact_digest": computed_digest,
            "records": records,
        }
        _atomic_write_json(self._post_state_path(cs_id), post_state)
        _atomic_write_json(self._post_hashes_path(cs_id), legacy_hashes)

    def record_rolled_back(self, cs_id: str) -> None:
        """Persist the durable provider completion marker (Item 7).

        The marker binds to the artifact digest and entry count so
        reconciliation can trust it as independent evidence that canonical
        rollback completed. Callers pass ``cs_id`` only — the marker is
        derived from the artifact itself.
        """
        art_path = self._art_path(cs_id)
        if art_path.exists():
            try:
                artifact = _read_json(art_path)
                files = artifact.get("files") or []
                entry_count = len(files)
            except Exception:
                entry_count = 0
            try:
                digest = self._artifact_digest(art_path)
            except Exception:
                digest = ""
        else:
            entry_count = 0
            digest = ""
        _atomic_write_json(
            self._rollback_result_path(cs_id),
            {
                "result_type": "rolled_back",
                "cs_id": cs_id,
                "artifact_digest": digest,
                "entry_count": entry_count,
                "adapter_version": self.reversible_adapter_version,
            },
        )

    def load_rollback_completion(self, cs_id: str) -> dict | None:
        """Load the provider rollback-completion marker (Item 7).

        Returns ``None`` if the marker is missing, unreadable, malformed, or
        the recorded evidence is contradictory. Callers should further bind
        the returned dict's ``artifact_digest`` and ``entry_count`` to
        trusted SQL evidence before finalizing.
        """
        path = self._rollback_result_path(cs_id)
        if not path.exists():
            return None
        try:
            data = _read_json(path)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("result_type") != "rolled_back":
            return None
        if data.get("cs_id") != cs_id:
            return None
        digest = data.get("artifact_digest")
        if not isinstance(digest, str) or len(digest) != 64:
            return None
        entry_count = data.get("entry_count")
        if not isinstance(entry_count, int) or isinstance(entry_count, bool) or entry_count <= 0:
            return None
        adapter_version = data.get("adapter_version")
        if adapter_version != self.reversible_adapter_version:
            return None
        return data

    # ------------------------------------------------------------------
    # Rollback preflight (Item 7)
    # ------------------------------------------------------------------

    def _preflight_rollback(
        self,
        cs_id: str,
        *,
        force: bool,
        expected_artifact_digest: str = "",
        expected_entry_count: int = 0,
    ) -> list[dict]:
        """Validate the entire rollback plan before any mutation.

        Returns an immutable-per-entry plan list on success. Raises
        :class:`RollbackArtifactInvalid`, :class:`PathEscapeError`, or
        :class:`RollbackPostStateUnavailable` on failure.
        """
        art_path = self._art_path(cs_id)
        if not art_path.exists():
            raise RollbackNotSupported(
                f"No rollback artifact for changeset {cs_id!r}"
            )
        try:
            artifact = _read_json(art_path)
        except Exception as exc:
            raise RollbackArtifactInvalid(
                f"Cannot read rollback artifact for {cs_id!r}: {exc}"
            ) from exc

        # Item 8: schema/version validation.
        version = artifact.get("version")
        if version is not None and version not in (
            ROLLBACK_ARTIFACT_VERSION, 1,
        ):
            raise RollbackArtifactInvalid(
                f"Unsupported artifact version {version!r} for {cs_id!r}"
            )
        if artifact.get("cs_id") not in (cs_id, None):
            raise RollbackArtifactInvalid(
                f"Artifact cs_id mismatch for {cs_id!r}"
            )
        files = artifact.get("files", [])
        if not isinstance(files, list) or not files:
            raise RollbackArtifactInvalid(
                f"Rollback artifact for {cs_id!r} has no entries."
            )

        # Item 8: bind to trusted digest if provided (from SQL metadata).
        actual_digest = self._artifact_digest(art_path)
        if expected_artifact_digest and expected_artifact_digest != actual_digest:
            raise RollbackArtifactInvalid(
                f"Artifact digest mismatch for {cs_id!r}"
            )
        # Item 4: bind to SQL-recorded entry count when provided.
        if expected_entry_count and len(files) != int(expected_entry_count):
            raise RollbackArtifactInvalid(
                f"Artifact entry count {len(files)} != expected "
                f"{expected_entry_count} for {cs_id!r}"
            )

        # Item 8: op index integrity — contiguous, unique, starting at 0.
        indexes = []
        for e in files:
            if not isinstance(e, dict):
                raise RollbackArtifactInvalid(
                    f"Non-dict entry in artifact for {cs_id!r}"
                )
            oi = e.get("op_index")
            if not isinstance(oi, int) or oi < 0:
                raise RollbackArtifactInvalid(
                    f"Bad op_index in artifact for {cs_id!r}"
                )
            indexes.append(oi)
        if sorted(indexes) != list(range(len(files))):
            raise RollbackArtifactInvalid(
                f"Op indexes not contiguous/unique for {cs_id!r}"
            )

        # Item 8: kind validation — only supported kinds.
        for e in files:
            k = e.get("kind", "")
            if k not in SUPPORTED_KINDS:
                raise RollbackArtifactInvalid(
                    f"Unsupported op kind {k!r} in artifact for {cs_id!r}"
                )

        # Item 9: duplicate canonical targets.
        seen: dict[str, int] = {}
        for e in files:
            rel = e.get("rel_path")
            if rel is None:
                canonical_str = e.get("canonical_path", "")
                if not canonical_str:
                    raise RollbackArtifactInvalid(
                        f"Entry missing rel_path in artifact for {cs_id!r}"
                    )
                rel = self._legacy_rel_from_canonical(canonical_str)
            if rel in seen:
                raise RollbackArtifactInvalid(
                    f"Duplicate canonical target {rel!r} in artifact for {cs_id!r}"
                )
            seen[rel] = int(e["op_index"])

        # For non-forced rollback we require a matching post-application state
        # bound to the same artifact digest and covering every entry.
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
            except Exception as exc:
                raise RollbackPostStateUnavailable(
                    f"Post-application state for {cs_id!r} is unreadable: {exc}"
                ) from exc
            state_digest = state_doc.get("artifact_digest", "")
            if state_digest and state_digest != actual_digest:
                raise RollbackPostStateUnavailable(
                    f"Post-state artifact digest mismatch for {cs_id!r}."
                )
            records = state_doc.get("records") or []
            if not isinstance(records, list) or not records:
                raise RollbackPostStateUnavailable(
                    f"Post-application state for {cs_id!r} has no records."
                )
            for r in records:
                if not isinstance(r, dict) or "op_index" not in r:
                    raise RollbackPostStateUnavailable(
                        f"Bad record in post-state for {cs_id!r}"
                    )
                state_by_index[int(r["op_index"])] = r
            if len(state_by_index) != len(files):
                raise RollbackPostStateUnavailable(
                    f"Post-application state for {cs_id!r} is incomplete: "
                    f"expected {len(files)} records, got {len(state_by_index)}."
                )

        snap_dir = self._snap_dir(cs_id)
        plan: list[dict] = []
        for entry in files:
            op_index = int(entry["op_index"])
            rel_path = entry.get("rel_path")
            if rel_path is None:
                canonical_str = entry.get("canonical_path", "")
                if not canonical_str:
                    raise RollbackArtifactInvalid(
                        f"Rollback entry for {cs_id!r} missing rel_path."
                    )
                rel_path = self._legacy_rel_from_canonical(canonical_str)
            # Item 4: validate rel_path through safe_resolve BEFORE any I/O.
            canonical = self._safe_resolve_content(rel_path)
            kind = entry.get("kind", "")
            existed = bool(entry.get("existed"))
            snap_name = entry.get("snap_name") or ""
            # Item 6: prefer deriving the expected snap name from the op index;
            # a mismatched persisted snap_name is a hard error.
            expected_snap_name = f"{op_index:04d}_{Path(rel_path).name}"
            if snap_name and snap_name != expected_snap_name:
                raise RollbackArtifactInvalid(
                    f"snap_name mismatch for op_index {op_index} in {cs_id!r}"
                )
            snap_name = snap_name or expected_snap_name
            snap_path = None
            if existed:
                # Item 6: validate snap path, verify digest against prepare().
                snap_path = self._safe_resolve_snap(snap_dir, snap_name)
                if not snap_path.exists():
                    raise RollbackArtifactInvalid(
                        f"Snapshot file missing for op_index {op_index} in {cs_id!r}"
                    )
                recorded_snap_hash = entry.get("snap_sha256", "") or ""
                if recorded_snap_hash:
                    actual_snap_hash = self._file_hash(snap_path)
                    if actual_snap_hash != recorded_snap_hash:
                        raise RollbackArtifactInvalid(
                            f"Snapshot digest mismatch for op_index {op_index}"
                            f" in {cs_id!r}"
                        )
            # Non-forced rollback: cross-check post-state.
            if not force:
                record = state_by_index[op_index]
                if record.get("rel_path") != rel_path:
                    raise RollbackPostStateUnavailable(
                        f"post-state rel_path mismatch for op_index {op_index}"
                    )
                if record.get("kind") != kind:
                    raise RollbackPostStateUnavailable(
                        f"post-state kind mismatch for op_index {op_index}"
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
            plan.append({
                "op_index": op_index,
                "canonical": canonical,
                "snap_path": snap_path,
                "existed": existed,
                "kind": kind,
                "rel_path": rel_path,
            })
        return plan

    def rollback(
        self,
        cs_id: str,
        *,
        force: bool = False,
        is_superuser: bool = False,
        expected_artifact_digest: str = "",
        expected_entry_count: int = 0,
    ) -> None:
        if force and not is_superuser:
            raise PermissionError("Forced rollback requires superuser privileges.")

        # Phase 1: complete preflight, no mutation.
        plan = self._preflight_rollback(
            cs_id,
            force=force,
            expected_artifact_digest=expected_artifact_digest,
            expected_entry_count=expected_entry_count,
        )

        # Phase 2: execute plan. Track compensation state.
        executed: list[tuple[Path, bytes | None]] = []
        try:
            for step in plan:
                canonical: Path = step["canonical"]
                snap_path = step["snap_path"]
                existed = step["existed"]
                kind = step["kind"]
                # Record what was on disk before we touch it (for compensation).
                if canonical.exists():
                    prev = canonical.read_bytes()
                else:
                    prev = None
                executed.append((canonical, prev))
                if existed:
                    if snap_path is not None and snap_path.exists():
                        canonical.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(snap_path, canonical)
                    elif canonical.exists() and kind == "create":
                        canonical.unlink()
                else:
                    if canonical.exists():
                        canonical.unlink()
        except Exception as exc:
            # Best-effort compensation: restore previously-recorded contents.
            for path, prev in reversed(executed):
                try:
                    if prev is None:
                        if path.exists():
                            path.unlink()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(prev)
                except Exception:
                    pass
            raise RollbackReconciliationRequired(
                f"Rollback Phase 2 failed after partial execution: {exc}"
            ) from exc

        # Item 5/6: canonical mutation has completed. If the provider
        # completion marker fails to persist we cannot signal "rollback
        # failed" — the file mutation already succeeded, so the caller must
        # treat this as ``reconciliation_required`` and use verify_rolled_
        # back_state to disambiguate.
        try:
            self.record_rolled_back(cs_id)
        except Exception as exc:
            raise RollbackReconciliationRequired(
                f"Rollback completed but provider marker failed: {exc}"
            ) from exc

    def verify_applied_state(
        self,
        cs_id: str,
        *,
        expected_artifact_digest: str = "",
        expected_entry_count: int = 0,
    ) -> VerificationResult:
        """Verify that on-disk state matches the recorded post-application state.

        Uses the provider-owned ``post_application_state.json`` record set so
        the check operates in the raw-file hash domain (not the semantic
        ContentItem domain).

        Item 3: verification is bound to trusted SQL evidence
        (``expected_artifact_digest`` and ``expected_entry_count``). Missing
        evidence returns ``"missing_evidence"``, never ``"verified"``.

        Item 4: post-application state is strict: every field must be present
        and typed correctly; ``expected_present`` must be a real ``bool``;
        ``sha256`` must be 64 hex chars when the file is expected present.
        """
        # Item 3: no trusted SQL evidence means we cannot verify.
        if not expected_artifact_digest or not expected_entry_count:
            return VerificationResult(
                status="missing_evidence",
                reason="No trusted SQL digest available",
            )

        state_path = self._post_state_path(cs_id)
        if not state_path.exists():
            return VerificationResult(
                status="missing_evidence",
                reason=f"No post_application_state.json for {cs_id!r}",
            )
        art_path = self._art_path(cs_id)
        if not art_path.exists():
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"No rollback_artifact.json for {cs_id!r}",
            )
        try:
            state_doc = _read_json(state_path)
            records = state_doc.get("records") or []
            artifact = _read_json(art_path)
            files = artifact.get("files") or []
        except Exception as exc:
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Cannot read post_application_state.json: {exc}",
            )
        if not records or not files:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Empty records or files list",
            )
        if len(records) != len(files):
            return VerificationResult(
                status="corrupt_evidence",
                reason=(
                    f"Record count {len(records)} does not match artifact "
                    f"entry count {len(files)}"
                ),
            )

        # Item 3: digest and count binding.
        actual_digest = self._artifact_digest(art_path)
        if actual_digest != expected_artifact_digest:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact digest does not match SQL evidence.",
            )
        if len(files) != int(expected_entry_count):
            return VerificationResult(
                status="corrupt_evidence",
                reason=(
                    f"Artifact entry count {len(files)} != expected "
                    f"{expected_entry_count}"
                ),
            )
        state_digest = state_doc.get("artifact_digest", "")
        if not state_digest or state_digest != actual_digest:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Post-state artifact digest does not match artifact.",
            )
        state_cs_id = state_doc.get("cs_id", "")
        if state_cs_id and state_cs_id != cs_id:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Post-state cs_id does not match request cs_id.",
            )
        state_version = state_doc.get("version")
        if state_version is not None and state_version not in (POST_STATE_VERSION, 1):
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Unsupported post-state version {state_version!r}",
            )

        # Item 4: unique contiguous op indexes.
        indexes = []
        for r in records:
            if not isinstance(r, dict):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason="Non-dict record in post-state.",
                )
            oi = r.get("op_index")
            if not isinstance(oi, int) or isinstance(oi, bool) or oi < 0:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"Bad op_index {oi!r} in post-state",
                )
            indexes.append(oi)
        if sorted(indexes) != list(range(len(records))):
            return VerificationResult(
                status="corrupt_evidence",
                reason="Post-state op indexes are not contiguous/unique.",
            )

        # Compare records to artifact entries by op_index.
        art_by_index = {int(e["op_index"]): e for e in files if isinstance(e, dict)}
        art_cs_id = artifact.get("cs_id", "")
        if art_cs_id and art_cs_id != cs_id:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact cs_id does not match request cs_id.",
            )
        art_version = artifact.get("version")
        if art_version is not None and art_version not in (ROLLBACK_ARTIFACT_VERSION, 1):
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Unsupported artifact version {art_version!r}",
            )

        details: dict = {"checked": len(records), "issues": []}
        for r in records:
            oi = r["op_index"]
            if oi not in art_by_index:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"post-state op_index {oi!r} not in artifact",
                )
            art_entry = art_by_index[oi]
            # Item 4: strict types on every field.
            for field_name in ("collection", "item_id", "kind", "rel_path"):
                if not isinstance(r.get(field_name), str):
                    return VerificationResult(
                        status="corrupt_evidence",
                        reason=f"Bad {field_name} at op_index {oi}",
                    )
            if r.get("kind") not in SUPPORTED_KINDS:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"Unsupported kind {r.get('kind')!r} at op_index {oi}",
                )
            # Every artifact field must match the post-state record.
            if art_entry.get("kind") != r.get("kind"):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"kind mismatch at op_index {oi}",
                )
            if (
                art_entry.get("rel_path") is not None
                and art_entry.get("rel_path") != r.get("rel_path")
            ):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path mismatch at op_index {oi}",
                )
            if (
                art_entry.get("collection") is not None
                and art_entry.get("collection") != r.get("collection")
            ):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"collection mismatch at op_index {oi}",
                )
            if (
                art_entry.get("item_id") is not None
                and art_entry.get("item_id") != r.get("item_id")
            ):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"item_id mismatch at op_index {oi}",
                )
            # Item 4: expected_present must be a real bool.
            expected_present_val = r.get("expected_present")
            if expected_present_val is not True and expected_present_val is not False:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"expected_present at op_index {oi} is not a bool",
                )
            expected_sha = r.get("sha256", "")
            if not isinstance(expected_sha, str):
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"sha256 at op_index {oi} is not a string",
                )
            # Item 4: contradiction check — create must have expected_present.
            if r["kind"] in ("create", "update") and expected_present_val is False:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=(
                        f"kind={r['kind']} but expected_present=False at "
                        f"op_index {oi}"
                    ),
                )
            if r["kind"] == "delete" and expected_present_val is True:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"kind=delete but expected_present=True at op_index {oi}",
                )
            rel = r["rel_path"]
            try:
                canonical = self._safe_resolve_content(rel)
            except Exception as exc:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path {rel!r} is unsafe: {exc}",
                    details=details,
                )
            if expected_present_val is True:
                if len(expected_sha) != 64 or any(
                    ch not in "0123456789abcdef" for ch in expected_sha
                ):
                    return VerificationResult(
                        status="corrupt_evidence",
                        reason=f"sha256 at op_index {oi} not a 64-char hex string",
                    )
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
                if expected_sha != "":
                    return VerificationResult(
                        status="corrupt_evidence",
                        reason=(
                            f"sha256 at op_index {oi} must be empty when "
                            "expected_present is False"
                        ),
                    )
                if canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "unexpected_present"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Expected file absent but present: {rel!r}",
                        details=details,
                    )
        return VerificationResult(status="verified", details=details)

    def verify_rolled_back_state(
        self,
        cs_id: str,
        *,
        expected_artifact_digest: str = "",
        expected_entry_count: int = 0,
    ) -> VerificationResult:
        """Verify that on-disk state matches the recorded pre-application state.

        Files whose pre-state was "did not exist" must be absent after rollback.
        Files whose pre-state was "existed" must match ``pre_hash`` after rollback.

        Item 3: verification is bound to trusted SQL evidence
        (``expected_artifact_digest`` and ``expected_entry_count``). Missing
        evidence returns ``"missing_evidence"``, never ``"verified"``.
        """
        # Item 3: require trusted SQL evidence.
        if not expected_artifact_digest or not expected_entry_count:
            return VerificationResult(
                status="missing_evidence",
                reason="No trusted SQL digest available",
            )
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
        if not files:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Empty artifact files list",
            )
        # Item 3: digest and count binding.
        actual_digest = self._artifact_digest(art_path)
        if actual_digest != expected_artifact_digest:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact digest does not match SQL evidence.",
            )
        if len(files) != int(expected_entry_count):
            return VerificationResult(
                status="corrupt_evidence",
                reason=(
                    f"Artifact entry count {len(files)} != expected "
                    f"{expected_entry_count}"
                ),
            )
        art_cs_id = artifact.get("cs_id", "")
        if art_cs_id and art_cs_id != cs_id:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact cs_id does not match request cs_id.",
            )
        art_version = artifact.get("version")
        if art_version is not None and art_version not in (ROLLBACK_ARTIFACT_VERSION, 1):
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Unsupported artifact version {art_version!r}",
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
                    rel = self._legacy_rel_from_canonical(canonical_str)
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
        # Inspect is a diagnostic view — call verify without trusted SQL evidence,
        # so both statuses will typically be "missing_evidence" in the raw view.
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

    def _legacy_rel_from_canonical(self, canonical_str: str) -> str:
        """Convert a legacy artifact's ``canonical_path`` to a safe rel_path.

        Item 5: legacy artifacts stored absolute paths. We accept them only
        after proving containment inside ``content_root`` — the absolute
        path is never trusted for I/O.
        """
        p = Path(canonical_str)
        if not p.is_absolute():
            # Legacy code always wrote absolute paths, but be forgiving if a
            # relative one leaked in: still resolve/validate.
            candidate = (self._content_root / p).resolve()
        else:
            candidate = p.resolve()
        try:
            rel = candidate.relative_to(self._content_root)
        except ValueError as exc:
            raise PathEscapeError(
                f"Legacy canonical_path escapes content_root: {canonical_str}"
            ) from exc
        return str(rel)

    def _canonical_path_for_op(self, op: Any) -> Path | None:
        """Best-effort resolution of the on-disk path for an operation.

        Item 4: collection segment is validated through ``safe_resolve``
        against ``content_root`` so pathological collection names cannot
        cause writes outside the content root.

        Item 13: collection and slug segments are validated through
        :func:`_identifiers.validate_identifier_segment` before any I/O.
        """
        try:
            from cauldron_content.contracts import ContentOperationKind
        except Exception:  # pragma: no cover - contract package must be available
            ContentOperationKind = None  # type: ignore[assignment]

        try:
            from ._identifiers import validate_identifier_segment
            validate_identifier_segment(op.collection, "collection")
        except Exception:
            return None
        try:
            coll_dir = safe_resolve(self._content_root, op.collection)
        except Exception:
            return None
        kind = op.kind
        if ContentOperationKind is not None and isinstance(kind, ContentOperationKind):
            kind_value = kind.value
        else:
            kind_value = str(kind)

        if kind_value == "create":
            slug = op.slug or op.item_id
            if not slug:
                return None
            try:
                validate_identifier_segment(slug, "slug")
            except Exception:
                return None
            try:
                return safe_resolve(coll_dir, f"{slug}.md")
            except Exception:
                return None
        if kind_value in ("update", "delete"):
            existing = self._find_file_for_item(op.collection, op.item_id)
            if existing is not None:
                return existing
            slug = op.slug or op.item_id
            if not slug:
                return None
            try:
                validate_identifier_segment(slug, "slug")
            except Exception:
                return None
            try:
                return safe_resolve(coll_dir, f"{slug}.md")
            except Exception:
                return None
        return None

    def _find_file_for_item(self, collection: str, item_id: str) -> Path | None:
        # Item 13: validate collection segment before any I/O.
        try:
            from ._identifiers import validate_identifier_segment
            validate_identifier_segment(collection, "collection")
        except Exception:
            return None
        try:
            coll_dir = safe_resolve(self._content_root, collection)
        except Exception:
            return None
        if not coll_dir.exists():
            return None
        try:
            import yaml  # type: ignore
        except Exception:
            return None
        content_root = self._content_root
        try:
            for f in coll_dir.glob("*.md"):
                # Item 12: harden against symlink escape and non-regular files.
                try:
                    resolved = f.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                try:
                    resolved.relative_to(content_root)
                except ValueError:
                    continue
                if not resolved.is_file():
                    continue
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
