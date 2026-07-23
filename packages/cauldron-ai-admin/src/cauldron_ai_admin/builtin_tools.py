"""Built-in Admin AI tools.

Six tools ship with the module. They are registered exactly once, when
``cauldron_ai_admin.apps.CauldronAIAdminConfig.ready()`` calls
:func:`register_builtin_tools`.

Every tool below is deliberately narrow. The service enforces the risk
policy and the persistent audit trail — handlers just do the work.
"""
from __future__ import annotations

import logging
from typing import Any

from .tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolResult,
    RiskLevel,
    get_tool_registry,
    unregister_tool,
)


logger = logging.getLogger(__name__)


ALLOWED_CHECK_TAGS = frozenset({
    "security",
    "database",
    "caches",
    "staticfiles",
    "templates",
    "urls",
    "models",
    "signals",
    "compatibility",
})

OWNING_MODULE = "cauldron.ai.admin"

BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    "content.list_collections",
    "content.list_items",
    "content.get_item",
    "content.create_proposal",
    "system.django_checks",
    "system.module_status",
)


# ---------------------------------------------------------------------------
# Service accessor — indirection so tests can swap the content service.
# ---------------------------------------------------------------------------


def _resolve_content_service(context: AdminAIToolContext) -> Any:
    """Look up the content-operations service.

    We first check ``context.actor`` for a test hook (``_content_service``)
    used by unit tests, then fall back to the standard admin factory.
    Callers get ``None`` when the service is not configured.
    """
    test_hook = getattr(context, "_content_service", None)
    if test_hook is not None:
        return test_hook
    try:
        from cauldron_admin_content.service_factory import get_service  # type: ignore
    except Exception:
        return None
    try:
        return get_service()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# content.list_collections
# ---------------------------------------------------------------------------

_LIST_COLLECTIONS_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _handle_list_collections(context: AdminAIToolContext, **kwargs) -> Any:
    if kwargs:
        return AdminAIToolError(
            tool_name="content.list_collections",
            error_code="tool.invalid_arguments",
            message="content.list_collections takes no arguments.",
        )
    svc = _resolve_content_service(context)
    if svc is None:
        return AdminAIToolError(
            tool_name="content.list_collections",
            error_code="content.service_unavailable",
            message="Content operations service is not available.",
        )
    try:
        names = svc.list_collections(user=context.actor)
    except Exception as exc:
        return AdminAIToolError(
            tool_name="content.list_collections",
            error_code="content.list_collections_failed",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    collections = [{"name": n} for n in (names or [])]
    return AdminAIToolResult(
        tool_name="content.list_collections",
        success=True,
        data={"collections": collections},
    )


# ---------------------------------------------------------------------------
# content.list_items
# ---------------------------------------------------------------------------

_LIST_ITEMS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "collection": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "offset": {"type": "integer", "minimum": 0},
        "include_drafts": {"type": "boolean"},
    },
    "required": ["collection"],
    "additionalProperties": False,
}


def _handle_list_items(context: AdminAIToolContext, **kwargs) -> Any:
    collection = kwargs.get("collection")
    limit = int(kwargs.get("limit", 20))
    offset = int(kwargs.get("offset", 0))
    include_drafts = bool(kwargs.get("include_drafts", False))

    if include_drafts and not context.actor.has_perm(
        "cauldron_content_operations.view_draft_content"
    ):
        return AdminAIToolError(
            tool_name="content.list_items",
            error_code="tool.permission_denied",
            message="Actor lacks view_draft_content permission.",
        )

    svc = _resolve_content_service(context)
    if svc is None:
        return AdminAIToolError(
            tool_name="content.list_items",
            error_code="content.service_unavailable",
            message="Content operations service is not available.",
        )
    try:
        items = svc.list_items(
            collection, user=context.actor, include_drafts=include_drafts,
        )
    except Exception as exc:
        return AdminAIToolError(
            tool_name="content.list_items",
            error_code="content.list_items_failed",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    serialised = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in (items or [])
    ]
    total = len(serialised)
    window = serialised[offset : offset + limit]
    return AdminAIToolResult(
        tool_name="content.list_items",
        success=True,
        data={"items": window, "total": total, "offset": offset},
    )


# ---------------------------------------------------------------------------
# content.get_item
# ---------------------------------------------------------------------------

_GET_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "collection": {"type": "string"},
        "item_id": {"type": "string"},
        "include_drafts": {"type": "boolean"},
    },
    "required": ["collection", "item_id"],
    "additionalProperties": False,
}


def _handle_get_item(context: AdminAIToolContext, **kwargs) -> Any:
    collection = kwargs.get("collection")
    item_id = kwargs.get("item_id")
    include_drafts = bool(kwargs.get("include_drafts", False))

    if include_drafts and not context.actor.has_perm(
        "cauldron_content_operations.view_draft_content"
    ):
        return AdminAIToolError(
            tool_name="content.get_item",
            error_code="tool.permission_denied",
            message="Actor lacks view_draft_content permission.",
        )

    svc = _resolve_content_service(context)
    if svc is None:
        return AdminAIToolError(
            tool_name="content.get_item",
            error_code="content.service_unavailable",
            message="Content operations service is not available.",
        )
    try:
        item = svc.get_item(
            item_id, collection, user=context.actor, include_drafts=include_drafts,
        )
    except Exception as exc:
        return AdminAIToolError(
            tool_name="content.get_item",
            error_code="content.get_item_failed",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    if item is None:
        return AdminAIToolResult(
            tool_name="content.get_item",
            success=True,
            data={"found": False},
        )
    data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
    return AdminAIToolResult(
        tool_name="content.get_item",
        success=True,
        data={"found": True, "item": data},
    )


# ---------------------------------------------------------------------------
# content.create_proposal
# ---------------------------------------------------------------------------

_CREATE_PROPOSAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "operations": {"type": "array"},
        "idempotency_key": {"type": "string"},
        "description": {"type": "string"},
        "provider_name": {"type": "string"},
    },
    "required": ["operations"],
    "additionalProperties": False,
}

# The only method a PROPOSE-level tool is ever allowed to call on the
# content-operations service. Kept as a module-level constant so tests
# can assert the invariant.
PROPOSAL_ALLOWED_METHODS: frozenset[str] = frozenset({"create_change_request"})


def _handle_create_proposal(context: AdminAIToolContext, **kwargs) -> Any:
    operations = kwargs.get("operations")
    if not isinstance(operations, list) or not operations:
        return AdminAIToolError(
            tool_name="content.create_proposal",
            error_code="tool.invalid_arguments",
            message="'operations' must be a non-empty list.",
        )
    idempotency_key = str(kwargs.get("idempotency_key", "") or "")
    description = str(kwargs.get("description", "") or "")
    provider_name = str(kwargs.get("provider_name", "") or "")

    svc = _resolve_content_service(context)
    if svc is None:
        return AdminAIToolError(
            tool_name="content.create_proposal",
            error_code="content.service_unavailable",
            message="Content operations service is not available.",
        )

    # Belt-and-braces guard: even if someone gave us the wrong shim,
    # a PROPOSE tool must only ever call create_change_request.
    if not hasattr(svc, "create_change_request"):
        return AdminAIToolError(
            tool_name="content.create_proposal",
            error_code="content.service_unsupported",
            message="Service does not expose create_change_request.",
        )

    try:
        result = svc.create_change_request(
            user=context.actor,
            operations=operations,
            provider_name=provider_name,
            description=description,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        return AdminAIToolError(
            tool_name="content.create_proposal",
            error_code="content.create_proposal_failed",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    if getattr(result, "ok", False):
        return AdminAIToolResult(
            tool_name="content.create_proposal",
            success=True,
            data={
                "cs_id": getattr(result, "request_id", ""),
                "status": "proposed",
            },
            message="Proposal created; awaiting human review.",
        )
    error = getattr(result, "error", None)
    return AdminAIToolError(
        tool_name="content.create_proposal",
        error_code=getattr(error, "code", "content.create_proposal_failed"),
        message=getattr(error, "message", "Failed to create proposal.")[:400],
    )


# ---------------------------------------------------------------------------
# system.django_checks
# ---------------------------------------------------------------------------

_DJANGO_CHECKS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
        },
    },
    "additionalProperties": False,
}

MAX_CHECK_FINDINGS = 50


def _handle_django_checks(context: AdminAIToolContext, **kwargs) -> Any:
    tags = kwargs.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return AdminAIToolError(
            tool_name="system.django_checks",
            error_code="tool.invalid_arguments",
            message="'tags' must be a list of strings.",
        )
    for t in tags:
        if t not in ALLOWED_CHECK_TAGS:
            return AdminAIToolError(
                tool_name="system.django_checks",
                error_code="tool.invalid_arguments",
                message=f"Tag {t!r} is not allow-listed.",
            )
    # Use Python API — never a subprocess.
    from django.core.checks.registry import registry as check_registry
    filtered = list(tags) if tags else []
    try:
        raw = check_registry.run_checks(tags=filtered) if filtered \
            else check_registry.run_checks()
    except Exception as exc:
        return AdminAIToolError(
            tool_name="system.django_checks",
            error_code="system.django_checks_failed",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    findings = []
    for message in list(raw)[:MAX_CHECK_FINDINGS]:
        findings.append({
            "id": getattr(message, "id", "") or "",
            "level": _django_check_level_label(message),
            "message": (getattr(message, "msg", "") or "")[:400],
            "hint": (getattr(message, "hint", "") or "")[:400] if getattr(
                message, "hint", "",
            ) else "",
        })
    return AdminAIToolResult(
        tool_name="system.django_checks",
        success=True,
        data={"findings": findings, "truncated": len(list(raw)) > MAX_CHECK_FINDINGS},
    )


def _django_check_level_label(message: Any) -> str:
    """Map django.core.checks levels onto strings we can share safely."""
    from django.core import checks as _checks
    level = getattr(message, "level", 0)
    if level >= _checks.CRITICAL:
        return "critical"
    if level >= _checks.ERROR:
        return "error"
    if level >= _checks.WARNING:
        return "warning"
    if level >= _checks.INFO:
        return "info"
    return "debug"


# ---------------------------------------------------------------------------
# system.module_status
# ---------------------------------------------------------------------------

_MODULE_STATUS_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _handle_module_status(context: AdminAIToolContext, **kwargs) -> Any:
    if kwargs:
        return AdminAIToolError(
            tool_name="system.module_status",
            error_code="tool.invalid_arguments",
            message="system.module_status takes no arguments.",
        )
    try:
        from cauldron.modules.registry import registry as module_registry
    except Exception as exc:
        return AdminAIToolError(
            tool_name="system.module_status",
            error_code="system.module_registry_unavailable",
            message=f"{type(exc).__name__}: {exc}"[:400],
        )
    graph = module_registry.graph_info() or []
    modules = []
    for entry in graph:
        modules.append({
            "name": entry.get("slug", ""),
            "capabilities": list(entry.get("provides", []) or []),
            "status": "active" if entry.get("active") else "inactive",
            "version": entry.get("version", ""),
        })
    return AdminAIToolResult(
        tool_name="system.module_status",
        success=True,
        data={"modules": modules},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


_BUILTIN_TOOL_DEFINITIONS: tuple[tuple[AdminAIToolDefinition, Any], ...] = (
    (
        AdminAIToolDefinition(
            name="content.list_collections",
            version="1.0",
            description="List content collections routed by the platform.",
            argument_schema=_LIST_COLLECTIONS_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_content_operations.view_published_content",
            owning_module=OWNING_MODULE,
        ),
        _handle_list_collections,
    ),
    (
        AdminAIToolDefinition(
            name="content.list_items",
            version="1.0",
            description="List items in a collection with pagination.",
            argument_schema=_LIST_ITEMS_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_content_operations.view_published_content",
            owning_module=OWNING_MODULE,
        ),
        _handle_list_items,
    ),
    (
        AdminAIToolDefinition(
            name="content.get_item",
            version="1.0",
            description="Fetch a single content item by id.",
            argument_schema=_GET_ITEM_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_content_operations.view_published_content",
            owning_module=OWNING_MODULE,
        ),
        _handle_get_item,
    ),
    (
        AdminAIToolDefinition(
            name="content.create_proposal",
            version="1.0",
            description=(
                "Create a content change-request proposal. The proposal is "
                "non-canonical and must be approved by a human before it "
                "can be applied."
            ),
            argument_schema=_CREATE_PROPOSAL_SCHEMA,
            risk_level=RiskLevel.PROPOSE,
            required_permission="cauldron_content_operations.propose_content_changes",
            owning_module=OWNING_MODULE,
            requires_human_approval=True,
        ),
        _handle_create_proposal,
    ),
    (
        AdminAIToolDefinition(
            name="system.django_checks",
            version="1.0",
            description=(
                "Run Django system checks for a fixed allow-list of tags and "
                "return a bounded summary of findings."
            ),
            argument_schema=_DJANGO_CHECKS_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_ai_admin.use_admin_ai",
            owning_module=OWNING_MODULE,
        ),
        _handle_django_checks,
    ),
    (
        AdminAIToolDefinition(
            name="system.module_status",
            version="1.0",
            description=(
                "Report discovered Cauldron modules, capabilities, and their "
                "activation status. No filesystem paths or environment "
                "variable values are ever exposed."
            ),
            argument_schema=_MODULE_STATUS_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_ai_admin.use_admin_ai",
            owning_module=OWNING_MODULE,
        ),
        _handle_module_status,
    ),
)


def register_builtin_tools() -> None:
    """Register every built-in tool with the singleton registry.

    Idempotent: if a tool is already registered under the same name (for
    example after a Django autoreload), we replace the entry with the
    fresh handler rather than raising.
    """
    reg = get_tool_registry()
    for definition, handler in _BUILTIN_TOOL_DEFINITIONS:
        existing = reg.get(definition.name)
        if existing is not None:
            # Replace to avoid stale handlers surviving a reload.
            unregister_tool(definition.name)
        reg.register(definition, handler)
