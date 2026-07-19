"""Tests for cauldron_content.contracts value types."""
import pytest

from cauldron_content.contracts import (
    ApplyResult,
    ContentChangeSet,
    ContentIdentity,
    ContentItem,
    ContentOperation,
    ContentOperationKind,
    ContentStatus,
    RepositoryHealth,
    ValidationIssue,
    ValidationResult,
)


def test_content_status_enum():
    assert ContentStatus.DRAFT == "draft"
    assert ContentStatus.PUBLISHED == "published"


def test_content_item_is_frozen():
    item = _make_item()
    with pytest.raises(Exception):
        item.id = "changed"  # type: ignore[misc]


def test_content_item_defensive_copies_data():
    src = {"title": "A"}
    item = _make_item(data=src)
    src["title"] = "B"
    assert item.data == {"title": "A"}


def test_content_item_defensive_copies_metadata():
    src = {"note": "one"}
    item = _make_item(metadata=src)
    src["note"] = "two"
    assert item.metadata == {"note": "one"}


def test_content_operation_defensive_copy():
    src = {"key": 1}
    op = ContentOperation(
        kind=ContentOperationKind.CREATE,
        provider="p",
        collection="c",
        item_id="id",
        data=src,
    )
    src["key"] = 99
    assert op.data == {"key": 1}


def test_content_changeset_defensive_copy_metadata():
    src = {"who": "me"}
    cs = ContentChangeSet(id="cs.1", operations=(), metadata=src)
    src["who"] = "them"
    assert cs.metadata == {"who": "me"}


def test_validation_result_ok():
    result = ValidationResult.ok()
    assert result.valid is True
    assert result.issues == ()


def test_validation_result_failed():
    issue = ValidationIssue(code="bad", message="nope")
    result = ValidationResult.failed([issue])
    assert result.valid is False
    assert result.issues == (issue,)


def test_repository_health_defensive_copy():
    details = {"note": "healthy"}
    h = RepositoryHealth(provider_name="p", healthy=True, details=details)
    details["note"] = "sick"
    assert h.details == {"note": "healthy"}


def test_apply_result_defaults():
    r = ApplyResult(success=True, applied=(), conflicts=(), validation_errors=())
    assert r.message == ""


def test_content_identity_immutable():
    ident = ContentIdentity(id="x", collection="c", slug="s")
    with pytest.raises(Exception):
        ident.id = "y"  # type: ignore[misc]


def _make_item(**overrides) -> ContentItem:
    defaults = {
        "id": "id.1",
        "collection": "coll",
        "slug": "slug",
        "status": ContentStatus.PUBLISHED,
        "schema": "s",
        "data": {"title": "A"},
        "body": "hello",
        "hash": "deadbeef",
        "provider": "flatfile",
    }
    defaults.update(overrides)
    return ContentItem(**defaults)  # type: ignore[arg-type]
