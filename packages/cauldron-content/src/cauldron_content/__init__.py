"""Cauldron content contracts and routing primitives."""

from .contracts import (
    ApplyResult,
    CollectionDefinition,
    Conflict,
    ContentChangeSet,
    ContentIdentity,
    ContentItem,
    ContentOperation,
    ContentOperationKind,
    ContentRepository,
    ContentStatus,
    RepositoryDescriptor,
    RepositoryHealth,
    ValidationIssue,
    ValidationResult,
)
from .hashing import compute_content_hash, normalize_body
from .registry import RegistrationError, RepositoryRegistry, registry
from .router import ContentRouter, RouterConfig, RouterError

__all__ = [
    "ApplyResult",
    "CollectionDefinition",
    "Conflict",
    "ContentChangeSet",
    "ContentIdentity",
    "ContentItem",
    "ContentOperation",
    "ContentOperationKind",
    "ContentRepository",
    "ContentStatus",
    "RepositoryDescriptor",
    "RepositoryHealth",
    "ValidationIssue",
    "ValidationResult",
    "compute_content_hash",
    "normalize_body",
    "RegistrationError",
    "RepositoryRegistry",
    "registry",
    "ContentRouter",
    "RouterConfig",
    "RouterError",
]
