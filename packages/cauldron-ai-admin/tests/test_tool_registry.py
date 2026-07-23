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
    r = AdminAIToolRegistry()
    r.register(_defn("a.b"), _handler)
    with pytest.raises(ValueError):
        r.register(_defn("a.b"), _handler)


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
    r.register(_defn("read", "app.view_x"), _handler)
    r.register(_defn("write", "app.edit_x"), _handler)

    class Actor:
        is_active = True
        def __init__(self, perms): self._perms = set(perms)
        def has_perm(self, perm): return perm in self._perms

    only_read = Actor(["app.view_x"])
    all_perms = Actor(["app.view_x", "app.edit_x"])
    assert [d.name for d in r.list_for_actor(only_read)] == ["read"]
    assert [d.name for d in r.list_for_actor(all_perms)] == ["read", "write"]


def test_list_for_actor_none_returns_empty():
    r = AdminAIToolRegistry()
    r.register(_defn("read"), _handler)
    assert r.list_for_actor(None) == []


def test_list_for_actor_inactive_returns_empty():
    r = AdminAIToolRegistry()
    r.register(_defn("read"), _handler)

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
        r.register(_defn("a"), "not callable")  # type: ignore[arg-type]


def test_definition_defensive_copy():
    schema = {"type": "object"}
    d = _defn("a")
    d = AdminAIToolDefinition(
        name="x",
        version="1",
        description="",
        argument_schema=schema,
        risk_level=RiskLevel.READ_ONLY,
        required_permission="p.q",
        owning_module="m",
    )
    schema["type"] = "mutated"
    assert d.argument_schema == {"type": "object"}


def test_definition_rejects_bad_risk_level():
    with pytest.raises(TypeError):
        AdminAIToolDefinition(
            name="x", version="1", description="", argument_schema={},
            risk_level="READ_ONLY",  # type: ignore[arg-type]
            required_permission="p.q", owning_module="m",
        )


def test_unregister_missing_is_silent():
    r = AdminAIToolRegistry()
    r.unregister("no-such-tool")  # no exception
