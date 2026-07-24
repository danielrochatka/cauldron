"""Tests for the ``server.*`` reserved namespace and idempotent registration."""
from __future__ import annotations

import pytest

from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
    SERVER_OWNING_MODULE,
)


def _handler(ctx, **kw):
    return AdminAIToolResult(tool_name="server.x", success=True)


def _defn(*, owning_module: str) -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name="server.launch",
        version="1.0",
        description="",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="cauldron_ai_admin.use_admin_ai",
        owning_module=owning_module,
    )


def test_server_namespace_reserved_for_server_module():
    r = AdminAIToolRegistry()
    with pytest.raises(ValueError):
        r.register(_defn(owning_module="cauldron.other.module"), _handler)


def test_server_namespace_allowed_for_server_module():
    r = AdminAIToolRegistry()
    r.register(_defn(owning_module=SERVER_OWNING_MODULE), _handler)
    entry = r.get("server.launch")
    assert entry is not None


def test_idempotent_reregistration_same_defn_and_handler():
    r = AdminAIToolRegistry()
    d = AdminAIToolDefinition(
        name="t.x", version="1.0", description="",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="a.b",
        owning_module="cauldron.m",
    )
    r.register(d, _handler)
    # Same handler + structurally-equivalent definition (though a new
    # instance) is silently allowed.
    d2 = AdminAIToolDefinition(
        name="t.x", version="1.0", description="",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="a.b",
        owning_module="cauldron.m",
    )
    r.register(d2, _handler)


def test_reregistration_with_different_handler_rejected():
    r = AdminAIToolRegistry()
    d = AdminAIToolDefinition(
        name="t.x", version="1.0", description="",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="a.b",
        owning_module="cauldron.m",
    )
    r.register(d, _handler)

    def _other_handler(ctx, **kw):  # pragma: no cover - not invoked
        return AdminAIToolResult(tool_name="t.x")

    with pytest.raises(ValueError):
        r.register(d, _other_handler)


def test_reregistration_with_different_definition_rejected():
    r = AdminAIToolRegistry()
    d = AdminAIToolDefinition(
        name="t.x", version="1.0", description="",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="a.b",
        owning_module="cauldron.m",
    )
    r.register(d, _handler)
    d2 = AdminAIToolDefinition(
        name="t.x", version="2.0", description="",  # different version
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="a.b",
        owning_module="cauldron.m",
    )
    with pytest.raises(ValueError):
        r.register(d2, _handler)
