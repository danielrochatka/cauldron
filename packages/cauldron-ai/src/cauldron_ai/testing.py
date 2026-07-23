"""Deterministic AI provider for tests only.

Callers pre-queue an ordered list of ``AIModelResponse`` objects. Each
``complete()`` call pops the next one and records the request that was
sent. This is not a Cauldron module — it is only meant to be imported
from tests or scripted local dev flows.
"""
from __future__ import annotations

from .contracts import AIModelRequest, AIModelResponse


class FakeAIModelProviderError(RuntimeError):
    """Raised when the fake provider is asked for a response it cannot give."""


class FakeAIModelProvider:
    """Deterministic provider for tests.

    Usage::

        fake = FakeAIModelProvider()
        fake.queue_response(AIModelResponse(
            provider_request_id="r1",
            content="hello",
            stop_reason="end_turn",
        ))
        response = fake.complete(request)
    """

    def __init__(self, name: str = "fake") -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("FakeAIModelProvider.name must be a non-empty string")
        self.name = name
        self._responses: list[AIModelResponse] = []
        self._requests: list[AIModelRequest] = []

    def queue_response(self, response: AIModelResponse) -> None:
        if not isinstance(response, AIModelResponse):
            raise TypeError("queue_response expects an AIModelResponse")
        self._responses.append(response)

    def complete(self, request: AIModelRequest) -> AIModelResponse:
        if not isinstance(request, AIModelRequest):
            raise TypeError("complete expects an AIModelRequest")
        self._requests.append(request)
        if not self._responses:
            raise FakeAIModelProviderError(
                "FakeAIModelProvider has no queued response"
            )
        return self._responses.pop(0)

    def was_called(self) -> bool:
        return bool(self._requests)

    def call_count(self) -> int:
        return len(self._requests)

    def last_request(self) -> AIModelRequest | None:
        return self._requests[-1] if self._requests else None

    def requests(self) -> list[AIModelRequest]:
        return list(self._requests)

    def reset(self) -> None:
        self._responses.clear()
        self._requests.clear()
