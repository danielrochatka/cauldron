"""FlatFileRepository — implements ContentRepository against a filesystem site."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from cauldron_content.contracts import (
    ApplyResult,
    Conflict,
    ContentChangeSet,
    ContentItem,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
    RepositoryDescriptor,
    RepositoryHealth,
    ValidationIssue,
    ValidationResult,
)
from cauldron_content.hashing import compute_content_hash, normalize_body

from ._paths import PathEscapeError, _safe_resolve
from .config import FlatFileCMSConfig
from .parser import parse_content_file
from .validator import SchemaError, load_schema, validate_item


def _valid_identifier_segment(value: str) -> bool:
    """Local identifier-segment guard (Item 13).

    Mirrors :func:`cauldron_content_operations._identifiers.validate_identifier_segment`
    without importing it, so cauldron-cms-flatfile stays independent.
    """
    if not isinstance(value, str) or value == "":
        return False
    if "/" in value or "\\" in value:
        return False
    if value.startswith(("/", "\\")):
        return False
    if value in (".", ".."):
        return False
    if any(ord(ch) < 0x20 for ch in value):
        return False
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return False
    return True

PROVIDER_NAME = "flatfile"


class FlatFileRepository:
    """Concrete flat-file content repository."""

    def __init__(self, config: FlatFileCMSConfig) -> None:
        self._config = config

    def describe(self) -> RepositoryDescriptor:
        return RepositoryDescriptor(
            provider_name=PROVIDER_NAME,
            label="Cauldron Flat-File CMS",
        )

    def list_collections(self) -> list[str]:
        content_dir = self._config.content_dir
        if not content_dir.exists():
            return []
        return sorted(p.name for p in content_dir.iterdir() if p.is_dir())

    def _load_collection(self, collection: str, include_drafts: bool) -> list[ContentItem]:
        # Item 13: reject unsafe collection segments before any I/O.
        if not _valid_identifier_segment(collection):
            return []
        # Item 4: containment-safe collection resolution — reject traversal,
        # absolute names, path separators embedded in the collection segment,
        # and symlink escapes.
        try:
            coll_dir = _safe_resolve(self._config.content_dir, collection)
        except Exception:
            return []
        if not coll_dir.exists():
            return []
        # Item 12: harden each discovered markdown file against symlink escape,
        # dangling links, and non-regular files (directories, FIFOs).
        content_root = Path(self._config.content_dir).resolve()
        items: list[ContentItem] = []
        seen_ids: dict[str, Path] = {}
        seen_slugs: dict[str, Path] = {}
        for md_file in sorted(coll_dir.glob("*.md")):
            try:
                resolved = md_file.resolve(strict=True)
            except (OSError, RuntimeError):
                # Dangling symlink or resolution failure — skip.
                continue
            try:
                resolved.relative_to(content_root)
            except ValueError:
                # Symlink whose target escapes content_root.
                continue
            if not resolved.is_file():
                # Reject directories, FIFOs, sockets, etc.
                continue
            item = parse_content_file(md_file, collection, PROVIDER_NAME)
            if item.id in seen_ids:
                raise ValueError(
                    f"Duplicate content ID {item.id!r} in {md_file} and {seen_ids[item.id]}"
                )
            if item.slug in seen_slugs:
                raise ValueError(
                    f"Duplicate slug {item.slug!r} in {md_file} and {seen_slugs[item.slug]}"
                )
            seen_ids[item.id] = md_file
            seen_slugs[item.slug] = md_file
            if not include_drafts and item.status == ContentStatus.DRAFT:
                continue
            items.append(item)
        return items

    def list_items(
        self, collection: str, *, include_drafts: bool = False
    ) -> list[ContentItem]:
        return self._load_collection(collection, include_drafts)

    def get_by_id(
        self,
        item_id: str,
        *,
        include_drafts: bool = False,
        collection: str = "",
    ) -> Optional[ContentItem]:
        # Item 3: when a collection is provided, restrict the search to that
        # collection. Otherwise fall back to a full-collection scan.
        if collection:
            for item in self._load_collection(collection, include_drafts=True):
                if item.id == item_id:
                    if not include_drafts and item.status == ContentStatus.DRAFT:
                        return None
                    return item
            return None
        for coll in self.list_collections():
            for item in self._load_collection(coll, include_drafts=True):
                if item.id == item_id:
                    if not include_drafts and item.status == ContentStatus.DRAFT:
                        return None
                    return item
        return None

    def get_by_slug(
        self, collection: str, slug: str, *, include_drafts: bool = False
    ) -> Optional[ContentItem]:
        for item in self._load_collection(collection, include_drafts=True):
            if item.slug == slug:
                if not include_drafts and item.status == ContentStatus.DRAFT:
                    return None
                return item
        return None

    def validate(self, item: ContentItem) -> ValidationResult:
        try:
            schema = load_schema(self._config.schema_dir, item.schema)
        except SchemaError as exc:
            return ValidationResult.failed(
                [
                    ValidationIssue(
                        code="schema_load_error",
                        message=str(exc),
                        collection=item.collection,
                        item_id=item.id,
                    )
                ]
            )
        return validate_item(item, schema)

    def apply(self, changeset: ContentChangeSet) -> ApplyResult:
        conflicts: list[Conflict] = []
        validation_errors: list[ValidationIssue] = []
        # Each staged entry: (target_path, content_str_or_None, result_item_or_None)
        staged: list[tuple[Path, Optional[str], Optional[ContentItem]]] = []

        for op in changeset.operations:
            result = self._stage_operation(op)
            if isinstance(result, Conflict):
                conflicts.append(result)
            elif isinstance(result, list):
                validation_errors.extend(result)
            else:
                staged.append(result)

        if conflicts or validation_errors:
            return ApplyResult(
                success=False,
                applied=(),
                conflicts=tuple(conflicts),
                validation_errors=tuple(validation_errors),
            )

        # Write files atomically. On failure, roll back writes.
        written: list[tuple[Path, Optional[bytes]]] = []
        applied_items: list[ContentItem] = []
        try:
            for path, content_text, result_item in staged:
                if content_text is None:
                    # DELETE
                    if path.exists():
                        backup = path.read_bytes()
                        written.append((path, backup))
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    backup = path.read_bytes() if path.exists() else None
                    written.append((path, backup))
                    _atomic_write_text(path, content_text)
                if result_item is not None:
                    applied_items.append(result_item)
        except Exception as exc:  # noqa: BLE001 - best-effort restoration
            for orig_path, backup_data in reversed(written):
                try:
                    if backup_data is None:
                        orig_path.unlink(missing_ok=True)
                    else:
                        _atomic_write_bytes(orig_path, backup_data)
                except Exception:
                    pass
            return ApplyResult(
                success=False,
                applied=(),
                conflicts=(),
                validation_errors=(),
                message=f"Apply failed: {exc}",
            )

        return ApplyResult(
            success=True,
            applied=tuple(applied_items),
            conflicts=(),
            validation_errors=(),
        )

    def _stage_operation(self, op: ContentOperation):
        # Item 13: reject unsafe collection segments before any I/O.
        if not _valid_identifier_segment(op.collection):
            return [
                ValidationIssue(
                    code="invalid_collection",
                    message=f"Invalid collection segment: {op.collection!r}",
                    collection=op.collection,
                    item_id=op.item_id,
                )
            ]
        # Item 4: containment-safe collection resolution.
        try:
            coll_dir = _safe_resolve(self._config.content_dir, op.collection)
        except Exception:
            return [
                ValidationIssue(
                    code="path_escape",
                    message="Collection path escapes content_dir",
                    collection=op.collection,
                    item_id=op.item_id,
                )
            ]

        if op.kind == ContentOperationKind.DELETE:
            # Item 3: resolve inside op.collection only so same-id items in
            # other collections cannot mis-route.
            existing = self.get_by_id(
                op.item_id, include_drafts=True, collection=op.collection,
            )
            if existing is None:
                return [
                    ValidationIssue(
                        code="not_found",
                        message=f"Item {op.item_id!r} not found",
                        collection=op.collection,
                        item_id=op.item_id,
                    )
                ]
            if op.expected_hash and not op.force and existing.hash != op.expected_hash:
                return Conflict(
                    item_id=op.item_id,
                    collection=op.collection,
                    expected_hash=op.expected_hash,
                    actual_hash=existing.hash,
                    message=f"Item {op.item_id!r} hash mismatch (stale).",
                )
            return (Path(existing.source_ref), None, None)

        if op.kind == ContentOperationKind.CREATE:
            slug = op.slug
            if not _valid_identifier_segment(slug):
                return [
                    ValidationIssue(
                        code="invalid_slug",
                        message=f"Invalid slug: {slug!r}",
                        collection=op.collection,
                        item_id=op.item_id,
                    )
                ]
            target = (coll_dir / f"{slug}.md").resolve()
            try:
                target.relative_to(self._config.content_dir)
            except ValueError:
                return [
                    ValidationIssue(
                        code="path_escape",
                        message="Target path escapes content_dir",
                        collection=op.collection,
                    )
                ]
            if target.exists():
                return [
                    ValidationIssue(
                        code="already_exists",
                        message=f"File already exists: {target.name}",
                        collection=op.collection,
                        item_id=op.item_id,
                    )
                ]
            for existing_item in self._load_collection(op.collection, include_drafts=True):
                if existing_item.id == op.item_id:
                    return [
                        ValidationIssue(
                            code="duplicate_id",
                            message=f"Item ID {op.item_id!r} already exists in collection {op.collection!r}",
                            collection=op.collection,
                            item_id=op.item_id,
                        )
                    ]
            body = normalize_body(op.body)
            result_item = ContentItem(
                id=op.item_id,
                collection=op.collection,
                slug=slug,
                status=op.status,
                schema=op.schema,
                data=dict(op.data),
                body=body,
                hash=compute_content_hash(
                    op.item_id, op.collection, slug, op.status.value, op.schema, op.data, body
                ),
                provider=PROVIDER_NAME,
                source_ref=str(target),
            )
            vr = self.validate(result_item)
            if not vr.valid:
                return list(vr.issues)
            return (target, _serialize_content_item(result_item), result_item)

        if op.kind == ContentOperationKind.UPDATE:
            # Item 3: resolve inside op.collection only.
            existing = self.get_by_id(
                op.item_id, include_drafts=True, collection=op.collection,
            )
            if existing is None:
                return [
                    ValidationIssue(
                        code="not_found",
                        message=f"Item {op.item_id!r} not found",
                        collection=op.collection,
                        item_id=op.item_id,
                    )
                ]
            if op.expected_hash and existing.hash != op.expected_hash:
                return Conflict(
                    item_id=op.item_id,
                    collection=op.collection,
                    expected_hash=op.expected_hash,
                    actual_hash=existing.hash,
                    message=f"Item {op.item_id!r} hash mismatch (stale).",
                )
            new_slug = op.slug or existing.slug
            if op.slug and op.slug != existing.slug:
                if not _valid_identifier_segment(op.slug):
                    return [
                        ValidationIssue(
                            code="invalid_slug",
                            message=f"Invalid slug: {op.slug!r}",
                            collection=op.collection,
                            item_id=op.item_id,
                        )
                    ]
                candidate = (coll_dir / f"{op.slug}.md").resolve()
                try:
                    candidate.relative_to(self._config.content_dir)
                except ValueError:
                    return [
                        ValidationIssue(
                            code="path_escape",
                            message="Target path escapes content_dir",
                            collection=op.collection,
                            item_id=op.item_id,
                        )
                    ]
                for sibling in self._load_collection(op.collection, include_drafts=True):
                    if sibling.id != op.item_id and sibling.slug == op.slug:
                        return [
                            ValidationIssue(
                                code="slug_conflict",
                                message=f"Slug {op.slug!r} is already used by item {sibling.id!r}",
                                collection=op.collection,
                                item_id=op.item_id,
                            )
                        ]
            merged_data = {**existing.data, **op.data}
            body = normalize_body(op.body) if op.body else existing.body
            new_schema = op.schema or existing.schema
            new_status = op.status
            result_item = ContentItem(
                id=op.item_id,
                collection=op.collection,
                slug=new_slug,
                status=new_status,
                schema=new_schema,
                data=merged_data,
                body=body,
                hash=compute_content_hash(
                    op.item_id,
                    op.collection,
                    new_slug,
                    new_status.value,
                    new_schema,
                    merged_data,
                    body,
                ),
                provider=PROVIDER_NAME,
                source_ref=existing.source_ref,
            )
            vr = self.validate(result_item)
            if not vr.valid:
                return list(vr.issues)
            return (Path(existing.source_ref), _serialize_content_item(result_item), result_item)

        return [
            ValidationIssue(
                code="unknown_op",
                message=f"Unknown operation kind: {op.kind!r}",
                collection=op.collection,
                item_id=op.item_id,
            )
        ]

    def health(self) -> RepositoryHealth:
        if not self._config.site_root.exists():
            return RepositoryHealth(PROVIDER_NAME, False, "site_root does not exist")
        if not self._config.content_dir.exists():
            return RepositoryHealth(
                PROVIDER_NAME,
                False,
                "content_dir does not exist",
                details={"content_dir": str(self._config.content_dir)},
            )
        return RepositoryHealth(PROVIDER_NAME, True, "Flat-file CMS is healthy")


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(tmp_path)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
        tmp = None
    finally:
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)


def _serialize_content_item(item: ContentItem) -> str:
    """Serialize a ContentItem back to Markdown with YAML front matter."""
    meta = {
        "id": item.id,
        "slug": item.slug,
        "status": item.status.value,
        "schema": item.schema,
        **item.data,
    }
    front_matter = yaml.safe_dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    return f"---\n{front_matter}---\n\n{item.body}"
