"""Tests for the AI provider registry."""
import pytest

from cauldron_ai.contracts import AIModelRequest, AIModelResponse
from cauldron_ai.providers import (
    AIModelProviderRegistry,
    ProviderRegistryError,
)


class _DummyProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, request: AIModelRequest) -> AIModelResponse:
        return AIModelResponse(provider_request_id="x")


def test_register_and_get():
    r = AIModelProviderRegistry()
    p = _DummyProvider("p1")
    r.register(p)
    assert r.get("p1") is p


def test_register_duplicate_raises():
    r = AIModelProviderRegistry()
    r.register(_DummyProvider("p1"))
    with pytest.raises(ProviderRegistryError):
        r.register(_DummyProvider("p1"))


def test_register_same_instance_is_idempotent():
    r = AIModelProviderRegistry()
    p = _DummyProvider("p1")
    r.register(p)
    r.register(p)  # same instance -> no error
    assert r.get("p1") is p


def test_get_unknown_raises():
    r = AIModelProviderRegistry()
    with pytest.raises(ProviderRegistryError):
        r.get("nope")


def test_default_empty_raises():
    r = AIModelProviderRegistry()
    with pytest.raises(ProviderRegistryError):
        r.default()


def test_default_single_returns_it():
    r = AIModelProviderRegistry()
    p = _DummyProvider("p1")
    r.register(p)
    assert r.default() is p


def test_default_ambiguous_raises():
    r = AIModelProviderRegistry()
    r.register(_DummyProvider("p1"))
    r.register(_DummyProvider("p2"))
    with pytest.raises(ProviderRegistryError):
        r.default()


def test_names_sorted():
    r = AIModelProviderRegistry()
    r.register(_DummyProvider("z"))
    r.register(_DummyProvider("a"))
    r.register(_DummyProvider("m"))
    assert r.names() == ["a", "m", "z"]


def test_unregister_silent_on_missing():
    r = AIModelProviderRegistry()
    r.unregister("no-such")  # no exception


def test_register_rejects_bad_shape():
    r = AIModelProviderRegistry()

    class NoName:
        def complete(self, req): return None

    class NoComplete:
        name = "x"

    with pytest.raises(ValueError):
        r.register(NoName())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        r.register(NoComplete())  # type: ignore[arg-type]


def test_module_singleton_isolated_from_tests():
    """The module-level singleton is separate from ad-hoc registries."""
    from cauldron_ai import providers

    providers._reset_registry_for_tests()
    assert providers.provider_names() == []
    providers.register_provider(_DummyProvider("solo"))
    try:
        assert providers.provider_names() == ["solo"]
        assert providers.get_default_provider().name == "solo"
    finally:
        providers._reset_registry_for_tests()
