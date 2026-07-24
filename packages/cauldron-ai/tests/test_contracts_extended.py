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
    assert call.arguments == {"outer": {"inner": [1, 2, 3]}}


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
    assert defn.parameters["required"] == ["a"]


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
