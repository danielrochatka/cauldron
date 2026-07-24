"""Tests for the AIModelProviderDescriptor registry API."""
from __future__ import annotations

import pytest

from cauldron_ai.contracts import AIModelRequest, AIModelResponse
from cauldron_ai.providers import (
    AIModelProviderDescriptor,
    AIModelProviderRegistry,
    ProviderRegistryError,
)


class _DummyProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, request: AIModelRequest) -> AIModelResponse:
        return AIModelResponse(provider_request_id="x")


def test_descriptor_defaults_to_provider_name():
    r = AIModelProviderRegistry()
    r.register(_DummyProvider("p1"))
    desc = r.descriptor_for("p1")
    assert desc.name == "p1"
    assert desc.display_name == "p1"


def test_descriptor_can_be_explicit():
    r = AIModelProviderRegistry()
    r.register(
        _DummyProvider("p1"),
        descriptor=AIModelProviderDescriptor(
            name="p1", display_name="Provider One", version="0.1.0",
        ),
    )
    desc = r.descriptor_for("p1")
    assert desc.display_name == "Provider One"
    assert desc.version == "0.1.0"


def test_descriptor_missing_raises():
    r = AIModelProviderRegistry()
    with pytest.raises(ProviderRegistryError):
        r.descriptor_for("nope")


def test_descriptor_name_must_match_provider_name():
    r = AIModelProviderRegistry()
    with pytest.raises(ValueError):
        r.register(
            _DummyProvider("p1"),
            descriptor=AIModelProviderDescriptor(
                name="different", display_name="", version="",
            ),
        )


def test_mismatched_descriptor_leaves_registry_unchanged():
    """A ValueError on register() must not leave the provider half-registered."""
    r = AIModelProviderRegistry()
    with pytest.raises(ValueError):
        r.register(
            _DummyProvider("p1"),
            descriptor=AIModelProviderDescriptor(
                name="different", display_name="", version="",
            ),
        )
    # Neither provider nor descriptor should be stored.
    assert r.names() == []
    with pytest.raises(ProviderRegistryError):
        r.get("p1")
    with pytest.raises(ProviderRegistryError):
        r.descriptor_for("p1")
    # And the "different" name obviously must not exist either.
    with pytest.raises(ProviderRegistryError):
        r.get("different")
