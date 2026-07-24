"""Contract-level tests for cauldron_ai.contracts."""
import dataclasses

import pytest

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
    AIModelToolCall,
    AIModelToolDefinition,
)


def test_message_valid_roles():
    for role in ("system", "user", "assistant"):
        m = AIModelMessage(role=role, content="hi")
        assert m.role == role


def test_message_tool_requires_tool_call_id():
    with pytest.raises(ValueError):
        AIModelMessage(role="tool", content="ok")
    m = AIModelMessage(role="tool", content="ok", tool_call_id="tc-1")
    assert m.tool_call_id == "tc-1"


def test_message_rejects_unknown_role():
    with pytest.raises(ValueError):
        AIModelMessage(role="hacker", content="x")


def test_message_is_immutable():
    m = AIModelMessage(role="user", content="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.content = "changed"  # type: ignore[misc]


def test_tool_call_arguments_copied():
    args = {"k": "v"}
    call = AIModelToolCall(id="c1", name="t", arguments=args)
    args["k"] = "mutated"
    assert call.arguments == {"k": "v"}


def test_tool_definition_parameters_copied():
    params = {"type": "object"}
    d = AIModelToolDefinition(name="t", description="d", parameters=params)
    params["type"] = "mutated"
    assert d.parameters == {"type": "object"}


def test_request_is_immutable():
    req = AIModelRequest(messages=(AIModelMessage(role="user", content="hi"),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.max_tokens = 1  # type: ignore[misc]


def test_response_is_immutable():
    resp = AIModelResponse(provider_request_id="p1", content="ok", stop_reason="end_turn")
    with pytest.raises(dataclasses.FrozenInstanceError):
        resp.content = "changed"  # type: ignore[misc]


def test_request_rejects_non_tuple_messages():
    with pytest.raises(TypeError):
        AIModelRequest(messages=[AIModelMessage(role="user", content="hi")])  # type: ignore[arg-type]


def test_response_rejects_bad_stop_reason():
    with pytest.raises(ValueError):
        AIModelResponse(provider_request_id="p", stop_reason="unknown")


def test_request_rejects_zero_max_tokens():
    with pytest.raises(ValueError):
        AIModelRequest(
            messages=(AIModelMessage(role="user", content="hi"),),
            max_tokens=0,
        )


def test_request_rejects_bad_timeout():
    with pytest.raises(ValueError):
        AIModelRequest(
            messages=(AIModelMessage(role="user", content="hi"),),
            timeout_seconds=0,
        )


def test_tool_call_rejects_empty_id():
    with pytest.raises(ValueError):
        AIModelToolCall(id="", name="t", arguments={})


def test_response_defaults():
    r = AIModelResponse(provider_request_id="p")
    assert r.content == ""
    assert r.tool_calls == ()
    assert r.input_tokens == 0
    assert r.output_tokens == 0
    assert r.stop_reason == ""
