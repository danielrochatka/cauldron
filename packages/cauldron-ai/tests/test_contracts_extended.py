"""Tests for the extended provider-neutral contracts.

These cover the role invariants on ``AIModelMessage``, the deep-frozen
copy semantics for tool call arguments and tool definition parameters,
and the new ``deadline_seconds`` field on ``AIModelRequest``.
"""
from __future__ import annotations

import pytest

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelToolCall,
    AIModelToolDefinition,
    is_json_serialisable,
)


# --------------------------------------------------------------------- role invariants


def test_user_message_rejects_tool_calls():
    with pytest.raises(ValueError):
        AIModelMessage(
            role="user",
            content="hi",
            tool_calls=(AIModelToolCall(id="c1", name="t.x", arguments={}),),
        )


def test_user_message_rejects_tool_call_id():
    with pytest.raises(ValueError):
        AIModelMessage(role="user", content="hi", tool_call_id="tc-1")


def test_system_message_rejects_tool_calls():
    with pytest.raises(ValueError):
        AIModelMessage(
            role="system",
            content="",
            tool_calls=(AIModelToolCall(id="c1", name="t.x", arguments={}),),
        )


def test_assistant_message_accepts_content_only():
    m = AIModelMessage(role="assistant", content="hello")
    assert m.content == "hello"
    assert m.tool_calls == ()


def test_assistant_message_accepts_tool_calls():
    tc = AIModelToolCall(id="c1", name="t.x", arguments={"k": "v"})
    m = AIModelMessage(role="assistant", tool_calls=(tc,))
    assert m.tool_calls == (tc,)


def test_assistant_message_accepts_both():
    tc = AIModelToolCall(id="c1", name="t.x", arguments={})
    m = AIModelMessage(role="assistant", content="thinking", tool_calls=(tc,))
    assert m.content == "thinking"
    assert m.tool_calls == (tc,)


def test_assistant_message_rejects_tool_call_id():
    with pytest.raises(ValueError):
        AIModelMessage(role="assistant", content="x", tool_call_id="tc-1")


def test_tool_message_requires_id_and_forbids_tool_calls():
    with pytest.raises(ValueError):
        AIModelMessage(role="tool", content="ok")  # no id
    with pytest.raises(ValueError):
        AIModelMessage(
            role="tool", content="", tool_call_id="tc-1",
            tool_calls=(AIModelToolCall(id="x", name="a.b", arguments={}),),
        )


# --------------------------------------------------------------------- deep freeze


def test_tool_call_arguments_deep_copy():
    nested = {"outer": {"inner": [1, 2, 3]}}
    call = AIModelToolCall(id="c1", name="t.x", arguments=nested)
    # Mutating the source dict must not affect the record.
    nested["outer"]["inner"].append(999)
    nested["outer"]["extra"] = "sneaky"
    # The stored record uses deep-frozen views (mappingproxy + tuple).
    assert call.arguments["outer"]["inner"] == (1, 2, 3)
    assert "extra" not in call.arguments["outer"]


def test_tool_definition_parameters_deep_copy():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    defn = AIModelToolDefinition(name="t.x", description="", parameters=schema)
    schema["properties"]["a"]["type"] = "mutated"
    schema["required"].append("b")
    assert defn.parameters["properties"]["a"]["type"] == "string"
    # Lists become tuples after deep freeze.
    assert defn.parameters["required"] == ("a",)


# --------------------------------------------------------------------- deadline


def test_request_default_deadline_is_none():
    req = AIModelRequest(messages=(AIModelMessage(role="user", content="hi"),))
    assert req.deadline_seconds is None


def test_request_accepts_positive_deadline():
    req = AIModelRequest(
        messages=(AIModelMessage(role="user", content="hi"),),
        deadline_seconds=12.5,
    )
    assert req.deadline_seconds == 12.5


def test_request_rejects_bad_deadline():
    with pytest.raises(ValueError):
        AIModelRequest(
            messages=(AIModelMessage(role="user", content="hi"),),
            deadline_seconds=0,
        )
    with pytest.raises(ValueError):
        AIModelRequest(
            messages=(AIModelMessage(role="user", content="hi"),),
            deadline_seconds=True,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------- json helper


def test_is_json_serialisable_true_for_primitives_and_containers():
    assert is_json_serialisable({"a": [1, 2, 3.0, None, True]})
    assert is_json_serialisable("")


def test_is_json_serialisable_false_for_non_json():
    class Custom:
        pass

    assert not is_json_serialisable({"o": Custom()})
    assert not is_json_serialisable({1, 2, 3})


# --------------------------------------------------------------------- immutability + strict JSON rejection


def test_tool_call_arguments_returned_dict_is_read_only():
    """The exposed ``arguments`` mapping must not be mutable at any depth."""
    call = AIModelToolCall(
        id="c1", name="t.x",
        arguments={"outer": {"inner": [1, 2]}},
    )
    with pytest.raises(TypeError):
        call.arguments["new"] = "sneaky"  # type: ignore[index]
    with pytest.raises(TypeError):
        call.arguments["outer"]["extra"] = "sneaky"  # type: ignore[index]
    # Tuples are the deep-frozen sequence type.
    assert call.arguments["outer"]["inner"] == (1, 2)


def test_tool_definition_parameters_returned_dict_is_read_only():
    defn = AIModelToolDefinition(
        name="t.x", description="",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    with pytest.raises(TypeError):
        defn.parameters["extra"] = "sneaky"  # type: ignore[index]
    with pytest.raises(TypeError):
        defn.parameters["properties"]["a"]["type"] = "mutated"  # type: ignore[index]


def test_tool_call_rejects_nan():
    with pytest.raises(ValueError):
        AIModelToolCall(id="c1", name="t.x", arguments={"v": float("nan")})


def test_tool_call_rejects_infinity():
    with pytest.raises(ValueError):
        AIModelToolCall(id="c1", name="t.x", arguments={"v": float("inf")})
    with pytest.raises(ValueError):
        AIModelToolCall(id="c1", name="t.x", arguments={"v": float("-inf")})


def test_tool_call_rejects_non_string_keys():
    with pytest.raises(ValueError):
        AIModelToolCall(id="c1", name="t.x", arguments={1: "v"})  # type: ignore[dict-item]


def test_tool_call_rejects_arbitrary_object():
    from datetime import datetime as _dt
    with pytest.raises((ValueError, TypeError)):
        AIModelToolCall(
            id="c1", name="t.x", arguments={"when": _dt(2026, 1, 1)},
        )


def test_tool_definition_rejects_nan_in_schema():
    with pytest.raises(ValueError):
        AIModelToolDefinition(
            name="t.x", description="",
            parameters={"const": float("nan")},
        )


def test_tool_definition_rejects_non_string_keys():
    with pytest.raises(ValueError):
        AIModelToolDefinition(
            name="t.x", description="",
            parameters={1: "v"},  # type: ignore[dict-item]
        )


def test_tool_call_valid_nested_structure_is_deep_frozen():
    call = AIModelToolCall(
        id="c1", name="t.x",
        arguments={
            "outer": {
                "list": [1, {"inner": True}, 3],
                "meta": {"count": 4},
            },
        },
    )
    # Every mapping is a MappingProxyType (immutable view).
    from types import MappingProxyType
    assert isinstance(call.arguments, MappingProxyType)
    assert isinstance(call.arguments["outer"], MappingProxyType)
    assert isinstance(call.arguments["outer"]["meta"], MappingProxyType)
    # Sequences are tuples.
    assert isinstance(call.arguments["outer"]["list"], tuple)
    # Innermost mapping still frozen.
    assert isinstance(call.arguments["outer"]["list"][1], MappingProxyType)
