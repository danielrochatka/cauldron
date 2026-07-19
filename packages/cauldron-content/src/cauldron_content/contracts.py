"""Value types describing content, repositories, and change operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


class ContentStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


@dataclass(frozen=True)
class ContentIdentity:
    id: str
    collection: str
    slug: str


@dataclass(frozen=True)
class ContentItem:
    id: str
    collection: str
    slug: str
    status: ContentStatus
    schema: str
    data: dict[str, Any]
    body: str
    hash: str
    provider: str
    source_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defensively copy mutable fields to preserve frozen semantics.
        object.__setattr__(self, "data", dict(self.data))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class CollectionDefinition:
    name: str
    schema: str
    provider: str


@dataclass(frozen=True)
class RepositoryDescriptor:
    provider_name: str
    label: str
    version: str = "0.1.0"
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    collection: str = ""
    item_id: str = ""
    source_path: str = ""
    json_path: str = ""
    schema_path: str = ""
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    issues: tuple[ValidationIssue, ...]

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True, issues=())

    @classmethod
    def failed(cls, issues: list[ValidationIssue]) -> "ValidationResult":
        return cls(valid=False, issues=tuple(issues))


class ContentOperationKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class ContentOperation:
    kind: ContentOperationKind
    provider: str
    collection: str
    item_id: str
    slug: str = ""
    expected_hash: str = ""  # optimistic concurrency; empty means "don't check"
    data: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    schema: str = ""
    status: ContentStatus = ContentStatus.DRAFT
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", dict(self.data))


@dataclass(frozen=True)
class ContentChangeSet:
    id: str
    operations: tuple[ContentOperation, ...]
    author: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class Conflict:
    item_id: str
    collection: str
    expected_hash: str
    actual_hash: str
    message: str


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    applied: tuple[ContentItem, ...]
    conflicts: tuple[Conflict, ...]
    validation_errors: tuple[ValidationIssue, ...]
    message: str = ""


@dataclass(frozen=True)
class RepositoryHealth:
    provider_name: str
    healthy: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))


@runtime_checkable
class ContentRepository(Protocol):
    """Structural protocol for content repositories."""

    def describe(self) -> RepositoryDescriptor: ...
    def list_collections(self) -> list[str]: ...
    def list_items(
        self, collection: str, *, include_drafts: bool = False
    ) -> list[ContentItem]: ...
    def get_by_id(
        self, item_id: str, *, include_drafts: bool = False
    ) -> Optional[ContentItem]: ...
    def get_by_slug(
        self, collection: str, slug: str, *, include_drafts: bool = False
    ) -> Optional[ContentItem]: ...
    def validate(self, item: ContentItem) -> ValidationResult: ...
    def apply(self, changeset: ContentChangeSet) -> ApplyResult: ...
    def health(self) -> RepositoryHealth: ...
