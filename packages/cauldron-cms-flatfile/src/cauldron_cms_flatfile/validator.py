"""JSON Schema validation for content items."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from cauldron_content.contracts import ContentItem, ValidationIssue, ValidationResult


class SchemaError(Exception):
    """Raised for missing or malformed schema files."""


def load_schema(schema_dir: Path, schema_name: str) -> dict:
    """Load a JSON Schema from ``schema_dir/{schema_name}.schema.json``."""
    schema_file = schema_dir / f"{schema_name}.schema.json"
    if not schema_file.exists():
        raise SchemaError(f"Schema {schema_name!r} not found at {schema_file}")
    try:
        with open(schema_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Invalid JSON in schema {schema_name!r}: {exc}") from exc


def validate_item(item: ContentItem, schema: dict) -> ValidationResult:
    """Validate ``item.data`` against the JSON Schema."""
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(item.data))
    if not errors:
        return ValidationResult.ok()
    issues = [
        ValidationIssue(
            code="schema_validation_error",
            message=str(e.message),
            collection=item.collection,
            item_id=item.id,
            json_path=str(getattr(e, "json_path", "") or ""),
            schema_path="/".join(str(p) for p in e.absolute_schema_path),
            severity="error",
        )
        for e in errors
    ]
    return ValidationResult.failed(issues)
