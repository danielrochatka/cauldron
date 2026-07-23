"""Tests for the child-module extension point.

Any Cauldron module can add a tool by importing
``cauldron_ai_admin.tools.register_tool`` and calling it in its
``AppConfig.ready()``. Admin AI itself never imports the child module.
"""
import sys
import types

import pytest


def test_child_module_can_register_tool_without_import_from_admin():
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolResult,
        RiskLevel,
        register_tool,
        get_tool_registry,
        unregister_tool,
    )

    # Build a synthetic child module in-memory. This stands in for a
    # real Cauldron module like `cauldron.ai.admin.server` that would
    # ship its own package. It only imports the public helpers.
    child = types.ModuleType("synthetic_child_module")

    def _register():
        register_tool(
            AdminAIToolDefinition(
                name="synthetic.hello",
                version="0.1",
                description="Child-registered tool",
                argument_schema={"type": "object"},
                risk_level=RiskLevel.READ_ONLY,
                required_permission="cauldron_ai_admin.use_admin_ai",
                owning_module="synthetic.child",
            ),
            lambda ctx, **kw: AdminAIToolResult(
                tool_name="synthetic.hello", success=True,
                data={"hello": True},
            ),
        )

    child.register = _register
    sys.modules["synthetic_child_module"] = child

    try:
        child.register()
        reg = get_tool_registry()
        entry = reg.get("synthetic.hello")
        assert entry is not None
        assert entry[0].owning_module == "synthetic.child"
    finally:
        unregister_tool("synthetic.hello")
        sys.modules.pop("synthetic_child_module", None)


def test_admin_ai_does_not_import_child_module_names():
    """The service module has no hard-coded knowledge of child packages."""
    import cauldron_ai_admin.service as svc_mod
    source = svc_mod.__file__
    with open(source, "r", encoding="utf-8") as f:
        text = f.read()
    # None of these are legitimate imports for the service.
    for bad in ("cauldron_ai_admin_server", "cauldron_admin_content",):
        assert bad not in text, f"{bad} must not appear in service.py"
