"""Tests for AdminAIToolRegistry."""
import pytest

from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _defn(name: str, perm: str = "cauldron_ai_admin.use_admin_ai") -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission=perm,
        owning_module="cauldron.ai.admin",
    )


def _handler(context: AdminAIToolContext, **kwargs) -> AdminAIToolResult:
    return AdminAIToolResult(tool_name="x", success=True)


def test_register_and_get():
    r = AdminAIToolRegistry()
    d = _defn("a.b")
    r.register(d, _handler)
    entry = r.get("a.b")
    assert entry is not None
    assert entry[0] is d
    assert entry[1] is _handler


def test_register_duplicate_raises():
    """Registering the same name with a different handler must fail."""
    r = AdminAIToolRegistry()
    r.register(_defn("a.b"), _handler)

    def _other_handler(ctx, **kw):  # pragma: no cover - not invoked
        return AdminAIToolResult(tool_name="x")

    with pytest.raises(ValueError):
        r.register(_defn("a.b"), _other_handler)


def test_register_idempotent_when_same_args():
    r = AdminAIToolRegistry()
    d = _defn("a.b")
    r.register(d, _handler)
    r.register(d, _handler)  # same args -> no raise
    assert r.get("a.b")[0] is d


def test_all_definitions_sorted():
    r = AdminAIToolRegistry()
    r.register(_defn("z.z"), _handler)
    r.register(_defn("a.a"), _handler)
    r.register(_defn("m.m"), _handler)
    names = [d.name for d in r.all_definitions()]
    assert names == ["a.a", "m.m", "z.z"]


def test_list_for_actor_filters_by_permission():
    r = AdminAIToolRegistry()
    r.register(_defn("t.read", "app.view_x"), _handler)
    r.register(_defn("t.write", "app.edit_x"), _handler)

    class Actor:
        is_active = True
        def __init__(self, perms): self._perms = set(perms)
        def has_perm(self, perm): return perm in self._perms

    only_read = Actor(["app.view_x"])
    all_perms = Actor(["app.view_x", "app.edit_x"])
    assert [d.name for d in r.list_for_actor(only_read)] == ["t.read"]
    assert [d.name for d in r.list_for_actor(all_perms)] == ["t.read", "t.write"]


def test_list_for_actor_none_returns_empty():
    r = AdminAIToolRegistry()
    r.register(_defn("t.read"), _handler)
    assert r.list_for_actor(None) == []


def test_list_for_actor_inactive_returns_empty():
    r = AdminAIToolRegistry()
    r.register(_defn("t.read"), _handler)

    class Actor:
        is_active = False
        def has_perm(self, perm): return True

    assert r.list_for_actor(Actor()) == []


def test_register_bad_definition_raises():
    r = AdminAIToolRegistry()
    with pytest.raises(TypeError):
        r.register("not a definition", _handler)  # type: ignore[arg-type]


def test_register_bad_handler_raises():
    r = AdminAIToolRegistry()
    with pytest.raises(TypeError):
        r.register(_defn("a.b"), "not callable")  # type: ignore[arg-type]


def test_definition_defensive_copy():
    schema = {"type": "object", "properties": {}}
    d = AdminAIToolDefinition(
        name="a.x",
        version="1.0",
        description="",
        argument_schema=schema,
        risk_level=RiskLevel.READ_ONLY,
        required_permission="p.q",
        owning_module="cauldron.m",
    )
    schema["type"] = "mutated"
    assert d.argument_schema == {"type": "object", "properties": {}}


def test_definition_rejects_bad_risk_level():
    with pytest.raises(TypeError):
        AdminAIToolDefinition(
            name="a.x", version="1.0", description="",
            argument_schema={"type": "object"},
            risk_level="READ_ONLY",  # type: ignore[arg-type]
            required_permission="p.q", owning_module="cauldron.m",
        )


def test_unregister_missing_is_silent():
    r = AdminAIToolRegistry()
    r.unregister("no-such-tool")  # no exception


# ---------------------------------------------------------- signature validation


def test_register_rejects_handler_with_no_params():
    r = AdminAIToolRegistry()

    def bad_handler():  # pragma: no cover - never invoked
        return None

    with pytest.raises(ValueError):
        r.register(_defn("a.b"), bad_handler)


def test_register_rejects_handler_with_star_args_first():
    r = AdminAIToolRegistry()

    def bad_handler(*args, **kwargs):  # pragma: no cover - never invoked
        return None

    with pytest.raises(ValueError):
        r.register(_defn("a.b"), bad_handler)


def test_register_rejects_var_positional_after_context():
    r = AdminAIToolRegistry()

    def bad_handler(ctx, *args, **kwargs):  # pragma: no cover - never invoked
        return None

    with pytest.raises(ValueError):
        r.register(_defn("a.b"), bad_handler)


def test_register_accepts_kw_only_after_context():
    r = AdminAIToolRegistry()

    def ok_handler(ctx, *, k=1):
        return AdminAIToolResult(tool_name="x", success=True)

    r.register(_defn("a.b"), ok_handler)


# ---------------------------- ITEM 2: handler/schema signature compatibility


def _schema_defn(name: str, schema: dict) -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name=name, version="1.0", description="",
        argument_schema=schema,
        risk_level=RiskLevel.READ_ONLY,
        required_permission="p.q",
        owning_module="cauldron.test",
    )


def test_register_rejects_handler_with_star_args_after_context():
    r = AdminAIToolRegistry()

    def bad_handler(ctx, *args, **kwargs):  # pragma: no cover
        return None

    with pytest.raises(ValueError):
        r.register(_schema_defn("a.b", {"type": "object"}), bad_handler)


def test_register_rejects_positional_only_second_param():
    r = AdminAIToolRegistry()
    # Build a handler with a positional-only second param via exec — the
    # syntax uses PEP 570 ``/``.
    ns: dict = {}
    exec(
        "def bad_handler(ctx, foo, /, **kw):\n    return None\n",
        ns,
    )
    bad_handler = ns["bad_handler"]
    with pytest.raises(ValueError):
        r.register(_schema_defn("a.b", {"type": "object"}), bad_handler)


def test_register_rejects_handler_required_kwarg_not_in_schema():
    r = AdminAIToolRegistry()

    def bad_handler(ctx, *, foo):  # required kwarg 'foo', schema has none
        return AdminAIToolResult(tool_name="a.b", success=True)

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    with pytest.raises(ValueError):
        r.register(_schema_defn("a.b", schema), bad_handler)


def test_register_accepts_handler_with_var_kwargs_and_any_schema():
    r = AdminAIToolRegistry()

    def any_handler(ctx, **kwargs):
        return AdminAIToolResult(tool_name="a.b", success=True)

    schema = {
        "type": "object",
        "properties": {"anything": {"type": "string"}},
    }
    r.register(_schema_defn("a.b", schema), any_handler)


def test_register_accepts_exact_schema_match():
    r = AdminAIToolRegistry()

    def ok_handler(ctx, *, collection, limit=None):
        return AdminAIToolResult(tool_name="a.b", success=True)

    schema = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["collection"],
    }
    r.register(_schema_defn("a.b", schema), ok_handler)


def test_register_rejects_schema_property_missing_from_handler():
    r = AdminAIToolRegistry()

    def narrow_handler(ctx, *, collection=None):
        return AdminAIToolResult(tool_name="a.b", success=True)

    schema = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "unexpected": {"type": "string"},
        },
    }
    with pytest.raises(ValueError):
        r.register(_schema_defn("a.b", schema), narrow_handler)


# ---------------------------------------------------------- builtin protection


def test_builtin_registration_cannot_overwrite_child_module_tool():
    """Once a child module has registered a tool at a given name, the
    subsequent builtin startup MUST NOT silently replace it."""
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition, RiskLevel, get_tool_registry,
        register_tool, unregister_tool,
    )

    reg = get_tool_registry()
    # Pre-register something at the builtin name with a distinct definition.
    child_defn = AdminAIToolDefinition(
        name="content.list_collections",
        version="0.9",  # different version -> different definition
        description="child-owned",
        argument_schema={"type": "object"},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="cauldron_content_operations.view_published_content",
        owning_module="child.module",
    )

    def child_handler(ctx, **kw):
        return AdminAIToolResult(tool_name="content.list_collections")

    # First: get a clean slate on this tool name
    unregister_tool("content.list_collections")
    register_tool(child_defn, child_handler)
    try:
        with pytest.raises(ValueError):
            register_builtin_tools()
    finally:
        unregister_tool("content.list_collections")
