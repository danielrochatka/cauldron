"""Tests for the deterministic fake AI provider."""
import pytest

from cauldron_ai.contracts import AIModelMessage, AIModelRequest, AIModelResponse
from cauldron_ai.testing import FakeAIModelProvider, FakeAIModelProviderError


def _req(text: str = "hi") -> AIModelRequest:
    return AIModelRequest(messages=(AIModelMessage(role="user", content=text),))


def test_queued_responses_returned_in_order():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(provider_request_id="r1", content="A"))
    fake.queue_response(AIModelResponse(provider_request_id="r2", content="B"))
    assert fake.complete(_req()).content == "A"
    assert fake.complete(_req()).content == "B"


def test_raises_when_queue_exhausted():
    fake = FakeAIModelProvider()
    with pytest.raises(FakeAIModelProviderError):
        fake.complete(_req())


def test_records_last_request():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(provider_request_id="r"))
    assert fake.last_request() is None
    r = _req("what's up")
    fake.complete(r)
    assert fake.last_request() is r
    assert fake.was_called()
    assert fake.call_count() == 1


def test_records_all_requests():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(provider_request_id="r1"))
    fake.queue_response(AIModelResponse(provider_request_id="r2"))
    r1, r2 = _req("one"), _req("two")
    fake.complete(r1)
    fake.complete(r2)
    assert fake.requests() == [r1, r2]


def test_reset_clears_state():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(provider_request_id="r1"))
    fake.complete(_req())
    fake.reset()
    assert not fake.was_called()
    with pytest.raises(FakeAIModelProviderError):
        fake.complete(_req())


def test_rejects_non_request_input():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(provider_request_id="r"))
    with pytest.raises(TypeError):
        fake.complete("not a request")  # type: ignore[arg-type]


def test_rejects_non_response_queue():
    fake = FakeAIModelProvider()
    with pytest.raises(TypeError):
        fake.queue_response("not a response")  # type: ignore[arg-type]


def test_name_defaults_to_fake():
    assert FakeAIModelProvider().name == "fake"


def test_custom_name():
    assert FakeAIModelProvider(name="fake-a").name == "fake-a"
