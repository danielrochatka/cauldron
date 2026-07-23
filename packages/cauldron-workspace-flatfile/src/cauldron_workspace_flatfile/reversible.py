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

from cauldron_content_operations.reversible import PreparationResult

from .config import WorkspaceConfig
from .paths import PathEscapeError, safe_resolve
from .store import _atomic_write_json, _read_json


ROLLBACK_ARTIFACT_VERSION = 2
POST_STATE_VERSION = 2

SUPPORTED_KINDS = frozenset({"create", "update", "delete"})


class EvidenceValidationError(Exception):
    """Raised when a rollback artifact or post-state document fails strict
    v2 validation.

    Callers that hold trusted SQL evidence catch this exception and translate
    it into ``rollback.artifact_invalid`` or ``VerificationResult(status=
    "mismatch"/"corrupt_evidence", ...)`` depending on the call site.
    """


@dataclass(frozen=True)
class RollbackEntry:
    """One validated entry in a v2 rollback artifact."""

    op_index: int
    collection: str
    item_id: str
    kind: str
    rel_path: str
    snap_name: str
    existed: bool
    pre_hash: str
    snap_sha256: str


@dataclass(frozen=True)
class PostStateRecord:
    """One validated record in a v2 post-application state document."""

    op_index: int
    collection: str
    item_id: str
    kind: str
    rel_path: str
    expected_present: bool
    sha256: str


def _is_hex64(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _is_positive_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    )


def parse_rollback_artifact(
    raw: dict,
    *,
    cs_id: str,
    trusted_digest: str,
    trusted_entry_count: int,
) -> list[RollbackEntry]:
    """Parse and fully validate a v2 rollback artifact.

    Enforces schema, digest binding, contiguous op-indexes, per-entry kinds,
    typed ``existed`` booleans, and per-branch hash requirements. Raises
    :class:`EvidenceValidationError` on any deviation.

    The parser is pure (no I/O) — the caller supplies the raw parsed JSON.
    """
    if not isinstance(raw, dict):
        raise EvidenceValidationError("rollback artifact is not an object")
    if not _is_hex64(trusted_digest):
        raise EvidenceValidationError("trusted_digest is missing or invalid")
    if not _is_positive_int(trusted_entry_count):
        raise EvidenceValidationError(
            "trusted_entry_count is missing or non-positive"
        )
    if "version" not in raw:
        raise EvidenceValidationError("artifact missing 'version'")
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise EvidenceValidationError(f"artifact version is not int: {version!r}")
    if version != ROLLBACK_ARTIFACT_VERSION:
        raise EvidenceValidationError(
            f"artifact version {version!r} is not {ROLLBACK_ARTIFACT_VERSION}"
        )
    if "cs_id" not in raw:
        raise EvidenceValidationError("artifact missing 'cs_id'")
    if raw.get("cs_id") != cs_id:
        raise EvidenceValidationError("artifact cs_id does not match request cs_id")

    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise EvidenceValidationError("artifact 'files' is missing or empty")
    if len(files) != trusted_entry_count:
        raise EvidenceValidationError(
            f"artifact entry count {len(files)} != trusted {trusted_entry_count}"
        )

    # Op-index contiguity check.
    indexes: list[int] = []
    for e in files:
        if not isinstance(e, dict):
            raise EvidenceValidationError("artifact entry is not an object")
        oi = e.get("op_index")
        if isinstance(oi, bool) or not isinstance(oi, int) or oi < 0:
            raise EvidenceValidationError(
                f"artifact op_index {oi!r} is not a non-negative int"
            )
        indexes.append(oi)
    if sorted(indexes) != list(range(len(files))):
        raise EvidenceValidationError(
            "artifact op_indexes are not contiguous unique 0-based"
        )

    entries: list[RollbackEntry] = []
    for entry in files:
        op_index = entry["op_index"]
        collection = entry.get("collection")
        if not isinstance(collection, str) or not collection:
            raise EvidenceValidationError(
                f"artifact collection missing/empty at op_index {op_index}"
            )
        item_id = entry.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise EvidenceValidationError(
                f"artifact item_id missing/empty at op_index {op_index}"
            )
        kind = entry.get("kind")
        if kind not in SUPPORTED_KINDS:
            raise EvidenceValidationError(
                f"artifact kind {kind!r} unsupported at op_index {op_index}"
            )
        rel_path = entry.get("rel_path")
        if not isinstance(rel_path, str) or not rel_path:
            raise EvidenceValidationError(
                f"artifact rel_path missing/empty at op_index {op_index}"
            )
        # Reject absolute paths and traversal at the parser level.
        rp = Path(rel_path)
        if rp.is_absolute():
            raise EvidenceValidationError(
                f"artifact rel_path is absolute at op_index {op_index}"
            )
        if any(part == ".." for part in rp.parts):
            raise EvidenceValidationError(
                f"artifact rel_path contains '..' at op_index {op_index}"
            )
        snap_name = entry.get("snap_name")
        expected_snap_name = f"snap_{op_index}.bin"
        if snap_name != expected_snap_name:
            raise EvidenceValidationError(
                f"artifact snap_name {snap_name!r} does not match "
                f"{expected_snap_name!r} at op_index {op_index}"
            )
        existed = entry.get("existed")
        if existed is not True and existed is not False:
            raise EvidenceValidationError(
                f"artifact existed is not a bool at op_index {op_index}"
            )
        pre_hash = entry.get("pre_hash")
        snap_sha256 = entry.get("snap_sha256")
        if existed is True:
            if not _is_hex64(pre_hash):
                raise EvidenceValidationError(
                    f"artifact pre_hash is not 64-char hex at op_index {op_index}"
                )
            if not _is_hex64(snap_sha256):
                raise EvidenceValidationError(
                    f"artifact snap_sha256 is not 64-char hex at op_index {op_index}"
                )
        else:
            if pre_hash != "":
                raise EvidenceValidationError(
                    f"artifact pre_hash must be '' when existed=False at op_index {op_index}"
                )
            if snap_sha256 != "":
                raise EvidenceValidationError(
                    f"artifact snap_sha256 must be '' when existed=False at op_index {op_index}"
                )
        entries.append(
            RollbackEntry(
                op_index=op_index,
                collection=collection,
                item_id=item_id,
                kind=kind,
                rel_path=rel_path,
                snap_name=snap_name,
                existed=existed,
                pre_hash=pre_hash,
                snap_sha256=snap_sha256,
            )
        )
    # Sort by op_index so callers can index by position.
    entries.sort(key=lambda e: e.op_index)
    return entries


def parse_post_state(
    raw: dict,
    *,
    cs_id: str,
    trusted_digest: str,
    rollback_entries: list[RollbackEntry],
) -> list[PostStateRecord]:
    """Parse and fully validate a v2 post-application state document.

    Enforces schema, digest binding, per-record types, and exact per-index
    parity with the corresponding :class:`RollbackEntry`. Raises
    :class:`EvidenceValidationError` on any deviation.
    """
    if not isinstance(raw, dict):
        raise EvidenceValidationError("post-state is not an object")
    if not _is_hex64(trusted_digest):
        raise EvidenceValidationError("trusted_digest is missing or invalid")
    if not isinstance(rollback_entries, list) or not rollback_entries:
        raise EvidenceValidationError("rollback_entries empty for post-state parse")

    if "version" not in raw:
        raise EvidenceValidationError("post-state missing 'version'")
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise EvidenceValidationError(
            f"post-state version is not int: {version!r}"
        )
    if version != POST_STATE_VERSION:
        raise EvidenceValidationError(
            f"post-state version {version!r} is not {POST_STATE_VERSION}"
        )
    if "cs_id" not in raw:
        raise EvidenceValidationError("post-state missing 'cs_id'")
    if raw.get("cs_id") != cs_id:
        raise EvidenceValidationError("post-state cs_id does not match request cs_id")

    if "artifact_digest" not in raw:
        raise EvidenceValidationError("post-state missing 'artifact_digest'")
    art_digest = raw["artifact_digest"]
    if art_digest != trusted_digest:
        raise EvidenceValidationError(
            "post-state artifact_digest does not match trusted digest"
        )

    records_raw = raw.get("records")
    if not isinstance(records_raw, list):
        raise EvidenceValidationError("post-state 'records' missing or not a list")
    if len(records_raw) != len(rollback_entries):
        raise EvidenceValidationError(
            f"post-state record count {len(records_raw)} != "
            f"{len(rollback_entries)} entries"
        )

    # Op-index contiguity.
    indexes: list[int] = []
    for r in records_raw:
        if not isinstance(r, dict):
            raise EvidenceValidationError("post-state record is not an object")
        oi = r.get("op_index")
        if isinstance(oi, bool) or not isinstance(oi, int) or oi < 0:
            raise EvidenceValidationError(
                f"post-state op_index {oi!r} is not a non-negative int"
            )
        indexes.append(oi)
    if sorted(indexes) != list(range(len(records_raw))):
        raise EvidenceValidationError(
            "post-state op_indexes are not contiguous unique 0-based"
        )

    entries_by_idx = {e.op_index: e for e in rollback_entries}
    records: list[PostStateRecord] = []
    for r in records_raw:
        op_index = r["op_index"]
        art_entry = entries_by_idx.get(op_index)
        if art_entry is None:
            raise EvidenceValidationError(
                f"post-state op_index {op_index} has no matching artifact entry"
            )
        collection = r.get("collection")
        if not isinstance(collection, str) or collection != art_entry.collection:
            raise EvidenceValidationError(
                f"post-state collection mismatch at op_index {op_index}"
            )
        item_id = r.get("item_id")
        if not isinstance(item_id, str) or item_id != art_entry.item_id:
            raise EvidenceValidationError(
                f"post-state item_id mismatch at op_index {op_index}"
            )
        kind = r.get("kind")
        if kind not in SUPPORTED_KINDS or kind != art_entry.kind:
            raise EvidenceValidationError(
                f"post-state kind mismatch at op_index {op_index}"
            )
        rel_path = r.get("rel_path")
        if not isinstance(rel_path, str) or rel_path != art_entry.rel_path:
            raise EvidenceValidationError(
                f"post-state rel_path mismatch at op_index {op_index}"
            )
        expected_present = r.get("expected_present")
        if expected_present is not True and expected_present is not False:
            raise EvidenceValidationError(
                f"post-state expected_present is not a bool at op_index {op_index}"
            )
        sha256 = r.get("sha256", "")
        if not isinstance(sha256, str):
            raise EvidenceValidationError(
                f"post-state sha256 is not a string at op_index {op_index}"
            )
        if kind in ("create", "update"):
            if expected_present is not True:
                raise EvidenceValidationError(
                    f"post-state {kind} requires expected_present=True at op_index {op_index}"
                )
            if not _is_hex64(sha256):
                raise EvidenceValidationError(
                    f"post-state sha256 is not 64-char hex at op_index {op_index}"
                )
        else:  # delete
            if expected_present is not False:
                raise EvidenceValidationError(
                    f"post-state delete requires expected_present=False at op_index {op_index}"
                )
            if sha256 != "":
                raise EvidenceValidationError(
                    f"post-state sha256 must be '' for delete at op_index {op_index}"
                )
        records.append(
            PostStateRecord(
                op_index=op_index,
                collection=collection,
                item_id=item_id,
                kind=kind,
                rel_path=rel_path,
                expected_present=expected_present,
                sha256=sha256,
            )
        )
    records.sort(key=lambda r: r.op_index)
    return records


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

            snap_name = f"snap_{i}.bin"
            kind_value = op.kind.value if hasattr(op.kind, "value") else str(op.kind)
            existed = canonical.exists()
            entry: dict = {
                "op_index": i,
                "snap_name": snap_name,
                "rel_path": rel,
                "collection": op.collection,
                "item_id": op.item_id,
                "kind": kind_value,
                "existed": bool(existed),
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
        art_path = self._art_path(cs_id)
        artifact = _read_json(art_path)
        # Compute the artifact digest so we can bind post-state to the artifact.
        computed_digest = self._artifact_digest(art_path)
        if artifact_digest and artifact_digest != computed_digest:
            raise RollbackPostStateUnavailable(
                f"artifact digest mismatch for {cs_id!r}."
            )
        # Item 3: parse the artifact strictly through the shared v2 parser.
        try:
            files_meta = artifact.get("files") or []
            entries = parse_rollback_artifact(
                artifact,
                cs_id=cs_id,
                trusted_digest=computed_digest,
                trusted_entry_count=len(files_meta),
            )
        except EvidenceValidationError as exc:
            raise RollbackPostStateUnavailable(
                f"Rollback artifact for {cs_id!r} failed v2 validation: {exc}"
            ) from exc

        records: list[dict] = []
        legacy_hashes: dict[str, str] = {}
        for entry in entries:
            canonical = self._safe_resolve_content(entry.rel_path)
            file_present = canonical.exists()
            if entry.kind in ("create", "update"):
                if not file_present:
                    raise RuntimeError(
                        f"Post-application contradiction: {entry.kind!r} target "
                        f"{entry.rel_path!r} does not exist on disk."
                    )
                sha = self._file_hash(canonical)
                records.append({
                    "op_index": entry.op_index,
                    "collection": entry.collection,
                    "item_id": entry.item_id,
                    "rel_path": entry.rel_path,
                    "kind": entry.kind,
                    "expected_present": True,
                    "sha256": sha,
                })
                legacy_hashes[entry.item_id] = sha
            elif entry.kind == "delete":
                records.append({
                    "op_index": entry.op_index,
                    "collection": entry.collection,
                    "item_id": entry.item_id,
                    "rel_path": entry.rel_path,
                    "kind": entry.kind,
                    "expected_present": False,
                    "sha256": "",
                })
                legacy_hashes[entry.item_id] = ""

        # Post-state binds itself to the artifact via the digest.
        post_state = {
            "version": POST_STATE_VERSION,
            "cs_id": cs_id,
            "artifact_digest": computed_digest,
            "records": records,
        }
        # Item 3: validate the post-state we just built through the same parser
        # so authors of record_applied cannot accidentally emit an invalid doc.
        try:
            parse_post_state(
                post_state,
                cs_id=cs_id,
                trusted_digest=computed_digest,
                rollback_entries=entries,
            )
        except EvidenceValidationError as exc:  # pragma: no cover - internal invariant
            raise RollbackPostStateUnavailable(
                f"Refusing to write invalid post-state for {cs_id!r}: {exc}"
            ) from exc
        _atomic_write_json(self._post_state_path(cs_id), post_state)
        _atomic_write_json(self._post_hashes_path(cs_id), legacy_hashes)

    def record_rolled_back(self, cs_id: str) -> None:
        """Persist the durable provider completion marker (Item 7).

        The marker binds to the artifact digest and entry count so
        reconciliation can trust it as independent evidence that canonical
        rollback completed. Callers pass ``cs_id`` only — the marker is
        derived from the artifact itself and validated through the shared
        parser before it is written.
        """
        art_path = self._art_path(cs_id)
        digest = ""
        entry_count = 0
        if art_path.exists():
            try:
                artifact = _read_json(art_path)
            except Exception:
                artifact = None
            if isinstance(artifact, dict):
                try:
                    digest = self._artifact_digest(art_path)
                except Exception:
                    digest = ""
                files = artifact.get("files")
                entry_count = len(files) if isinstance(files, list) else 0
                # Item 3: strictly validate through the shared parser; a
                # tampered artifact is never used to derive a completion
                # marker.
                if digest and entry_count:
                    try:
                        parse_rollback_artifact(
                            artifact,
                            cs_id=cs_id,
                            trusted_digest=digest,
                            trusted_entry_count=entry_count,
                        )
                    except EvidenceValidationError:
                        # Marker still records what we know but rejects
                        # obviously bad evidence up front.
                        digest = ""
                        entry_count = 0
        marker = {
            "result_type": "rolled_back",
            "cs_id": cs_id,
            "artifact_digest": digest,
            "entry_count": entry_count,
            "adapter_version": self.reversible_adapter_version,
        }
        _atomic_write_json(self._rollback_result_path(cs_id), marker)

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
        if not _is_hex64(digest):
            return None
        entry_count = data.get("entry_count")
        if not _is_positive_int(entry_count):
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
        expected_artifact_digest: str,
        expected_entry_count: int,
    ) -> list[dict]:
        """Validate the entire rollback plan before any mutation.

        Returns an immutable-per-entry plan list on success. Raises
        :class:`RollbackArtifactInvalid`, :class:`PathEscapeError`,
        :class:`RollbackNotSupported`, or :class:`RollbackPostStateUnavailable`
        on failure.

        Item 3: uses the shared :func:`parse_rollback_artifact` and
        :func:`parse_post_state` parsers. Trusted digest and entry count are
        mandatory keyword arguments — the adapter rejects missing evidence
        directly (adapter callers cannot silently opt out).
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

        # Item 3: adapter callers MUST supply trusted evidence; without it we
        # cannot bind the artifact to SQL truth.
        actual_digest = self._artifact_digest(art_path)
        if not _is_hex64(expected_artifact_digest):
            raise RollbackArtifactInvalid(
                f"expected_artifact_digest missing or invalid for {cs_id!r}"
            )
        if actual_digest != expected_artifact_digest:
            raise RollbackArtifactInvalid(
                f"Artifact digest mismatch for {cs_id!r}"
            )
        if not _is_positive_int(expected_entry_count):
            raise RollbackArtifactInvalid(
                f"expected_entry_count missing or non-positive for {cs_id!r}"
            )
        try:
            entries = parse_rollback_artifact(
                artifact,
                cs_id=cs_id,
                trusted_digest=actual_digest,
                trusted_entry_count=int(expected_entry_count),
            )
        except EvidenceValidationError as exc:
            raise RollbackArtifactInvalid(
                f"Rollback artifact invalid for {cs_id!r}: {exc}"
            ) from exc

        # Item 9: duplicate canonical targets across entries.
        seen_rels: dict[str, int] = {}
        for e in entries:
            if e.rel_path in seen_rels:
                raise RollbackArtifactInvalid(
                    f"Duplicate canonical target {e.rel_path!r} in artifact for {cs_id!r}"
                )
            seen_rels[e.rel_path] = e.op_index

        # For non-forced rollback we require a matching post-application state
        # bound to the same artifact digest and covering every entry.
        state_by_index: dict[int, PostStateRecord] = {}
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
            try:
                records = parse_post_state(
                    state_doc,
                    cs_id=cs_id,
                    trusted_digest=actual_digest,
                    rollback_entries=entries,
                )
            except EvidenceValidationError as exc:
                raise RollbackPostStateUnavailable(
                    f"Post-application state invalid for {cs_id!r}: {exc}"
                ) from exc
            state_by_index = {r.op_index: r for r in records}

        snap_dir = self._snap_dir(cs_id)
        plan: list[dict] = []
        for entry in entries:
            op_index = entry.op_index
            rel_path = entry.rel_path
            # Item 4: validate rel_path through safe_resolve BEFORE any I/O.
            canonical = self._safe_resolve_content(rel_path)
            kind = entry.kind
            existed = entry.existed
            snap_name = entry.snap_name
            snap_path = None
            if existed:
                # Item 6: validate snap path, verify digest against prepare().
                snap_path = self._safe_resolve_snap(snap_dir, snap_name)
                if not snap_path.exists():
                    raise RollbackArtifactInvalid(
                        f"Snapshot file missing for op_index {op_index} in {cs_id!r}"
                    )
                actual_snap_hash = self._file_hash(snap_path)
                if actual_snap_hash != entry.snap_sha256:
                    raise RollbackArtifactInvalid(
                        f"Snapshot digest mismatch for op_index {op_index}"
                        f" in {cs_id!r}"
                    )
            # Non-forced rollback: cross-check post-state.
            if not force:
                record = state_by_index[op_index]
                expected_present = record.expected_present
                expected_sha = record.sha256
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
        evidence returns ``"missing_evidence"``, never ``"verified"``. Any
        :class:`EvidenceValidationError` from the shared parsers is surfaced
        as ``"mismatch"``.
        """
        # Item 3: no trusted SQL evidence means we cannot verify.
        if not expected_artifact_digest or not expected_entry_count:
            return VerificationResult(
                status="missing_evidence",
                reason="No trusted SQL digest available",
            )
        if not _is_hex64(expected_artifact_digest) or not _is_positive_int(expected_entry_count):
            return VerificationResult(
                status="missing_evidence",
                reason="Trusted evidence is malformed",
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
            artifact = _read_json(art_path)
        except Exception as exc:
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Cannot read state or artifact: {exc}",
            )
        actual_digest = self._artifact_digest(art_path)
        if actual_digest != expected_artifact_digest:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact digest does not match SQL evidence.",
            )
        # Item 3: parse both documents through the shared parser.
        try:
            entries = parse_rollback_artifact(
                artifact,
                cs_id=cs_id,
                trusted_digest=actual_digest,
                trusted_entry_count=int(expected_entry_count),
            )
            records = parse_post_state(
                state_doc,
                cs_id=cs_id,
                trusted_digest=actual_digest,
                rollback_entries=entries,
            )
        except EvidenceValidationError as exc:
            return VerificationResult(
                status="mismatch",
                reason=str(exc)[:200],
            )

        details: dict = {"checked": len(records), "issues": []}
        for record in records:
            rel = record.rel_path
            try:
                canonical = self._safe_resolve_content(rel)
            except Exception as exc:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path {rel!r} is unsafe: {exc}",
                    details=details,
                )
            if record.expected_present:
                if not canonical.exists():
                    details["issues"].append({"rel_path": rel, "reason": "missing"})
                    return VerificationResult(
                        status="mismatch",
                        reason=f"Expected file present but missing: {rel!r}",
                        details=details,
                    )
                actual = self._file_hash(canonical)
                if actual != record.sha256:
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
        evidence returns ``"missing_evidence"``, never ``"verified"``. Any
        :class:`EvidenceValidationError` from the shared parser surfaces as
        ``"mismatch"``.
        """
        # Item 3: require trusted SQL evidence.
        if not expected_artifact_digest or not expected_entry_count:
            return VerificationResult(
                status="missing_evidence",
                reason="No trusted SQL digest available",
            )
        if not _is_hex64(expected_artifact_digest) or not _is_positive_int(expected_entry_count):
            return VerificationResult(
                status="missing_evidence",
                reason="Trusted evidence is malformed",
            )
        art_path = self._art_path(cs_id)
        if not art_path.exists():
            return VerificationResult(
                status="missing_evidence",
                reason=f"No rollback_artifact.json for {cs_id!r}",
            )
        try:
            artifact = _read_json(art_path)
        except Exception as exc:
            return VerificationResult(
                status="corrupt_evidence",
                reason=f"Cannot read rollback_artifact.json: {exc}",
            )
        actual_digest = self._artifact_digest(art_path)
        if actual_digest != expected_artifact_digest:
            return VerificationResult(
                status="corrupt_evidence",
                reason="Artifact digest does not match SQL evidence.",
            )
        try:
            entries = parse_rollback_artifact(
                artifact,
                cs_id=cs_id,
                trusted_digest=actual_digest,
                trusted_entry_count=int(expected_entry_count),
            )
        except EvidenceValidationError as exc:
            return VerificationResult(
                status="mismatch",
                reason=str(exc)[:200],
            )
        details: dict = {"checked": len(entries), "issues": []}
        for entry in entries:
            rel = entry.rel_path
            try:
                canonical = self._safe_resolve_content(rel)
            except Exception as exc:
                return VerificationResult(
                    status="corrupt_evidence",
                    reason=f"rel_path unsafe: {exc}",
                    details=details,
                )
            if not entry.existed:
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
                if actual != entry.pre_hash:
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
