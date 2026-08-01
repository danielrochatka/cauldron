"""Regression tests for ``_resolve_provider_with_factory``.

These tests pin down the Phase-2 hardening behaviour: the service factory
routes every provider name through ``build_provider`` so both static
instances and factory registrations share a single dispatch path, and
construction failures surface as credential-safe ``ImproperlyConfigured``
messages.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from cauldron_ai.testing import reset_provider_registry_for_tests


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_provider_registry_for_tests()
    yield
    reset_provider_registry_for_tests()


@pytest.fixture()
def store(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        _reset_store_for_tests,
    )
    p = tmp_path / "ai.json"
    _reset_store_for_tests(path=p)
    yield AIProviderSettingsStore(p)
    _reset_store_for_tests(path=None)


class _FakeProvider:
    """Trivial static provider used as the "instance registered" case."""

    name = "fake"
    display_name = "Fake"

    def complete(self, req):  # pragma: no cover - never called here
        return None


def _make_openai_style_factory(*, build_should_raise: Exception | None = None):
    """Return a factory that reports it built (or raises the given error)."""
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )
    from cauldron_ai.provider_configuration import AIProviderConfigurationError

    build_calls: list[dict] = []

    class _F:
        name = "openai"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="openai",
                display_name="OpenAI",
                fields=(
                    AIProviderConfigurationField(
                        name="model", label="Model", required=True,
                    ),
                    AIProviderConfigurationField(
                        name="api_key", label="Key",
                        field_type=FIELD_TYPE_PASSWORD, required=True,
                    ),
                ),
                supports_connection_test=True,
            )

        def build(self, config, secrets):
            build_calls.append({"config": dict(config), "secrets": dict(secrets)})
            if build_should_raise is not None:
                raise build_should_raise
            # If the caller didn't supply an api_key, mimic the real
            # OpenAI factory's fixed configuration-error message.
            if not secrets.get("api_key"):
                raise AIProviderConfigurationError(
                    "OpenAI provider: api_key is required."
                )

            class _P:
                name = "openai"
                display_name = "OpenAI"

                def complete(self, req):  # pragma: no cover
                    return None

            return _P()

        def test_connection(self, config, secrets):
            return AIProviderConnectionResult(success=True, status="ok")

    factory = _F()
    return factory, build_calls


# ---------------------------------------------------------------------------
# Static provider path
# ---------------------------------------------------------------------------

def test_fake_resolves_as_static_provider(store):
    from cauldron_ai.providers import register_provider
    from cauldron_ai_admin.service_factory import _resolve_provider_with_factory

    fake = _FakeProvider()
    register_provider(fake)
    resolved = _resolve_provider_with_factory("fake", store)
    # ``build_provider`` returns the exact same instance for static
    # registrations — no proxy, no copy.
    assert resolved is fake


# ---------------------------------------------------------------------------
# Factory-backed path
# ---------------------------------------------------------------------------

def test_openai_resolves_through_factory(store):
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.service_factory import _resolve_provider_with_factory

    factory, calls = _make_openai_style_factory()
    register_provider_factory(factory)
    store.set_config("openai", {"model": "gpt-4o-mini"})
    store.set_secret("openai", "api_key", "sk-test")

    provider = _resolve_provider_with_factory("openai", store)
    assert getattr(provider, "name", "") == "openai"
    # The factory's build() was invoked exactly once with the stored
    # credentials — proving the unified dispatch reached the factory
    # rather than dying on ``get_provider("openai")``.
    assert len(calls) == 1
    assert calls[0]["config"]["model"] == "gpt-4o-mini"
    assert calls[0]["secrets"]["api_key"] == "sk-test"


def test_unknown_provider_fails_safely(store):
    from cauldron_ai_admin.service_factory import _resolve_provider_with_factory

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _resolve_provider_with_factory("does-not-exist", store)
    # Message must be safe: no exception class name, no api key hints.
    msg = str(excinfo.value)
    assert "does-not-exist" in msg
    assert "Check your AI settings" in msg


def test_missing_openai_config_fails_safely(store):
    """Missing api_key raises a fixed configuration-error string."""
    from cauldron_ai.provider_configuration import AIProviderConfigurationError
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.service_factory import _resolve_provider_with_factory

    factory, _calls = _make_openai_style_factory()
    register_provider_factory(factory)
    # No secrets stored — the factory raises AIProviderConfigurationError.
    with pytest.raises(AIProviderConfigurationError) as excinfo:
        _resolve_provider_with_factory("openai", store)
    msg = str(excinfo.value)
    # The message is the fixed one the factory produced — never a raw
    # SDK traceback.
    assert "api_key" in msg
    # And the message must not contain a stray credential from the store.
    assert "sk-" not in msg


def test_unexpected_build_failure_is_scrubbed(store):
    """An unexpected exception from build() is collapsed to a safe message."""
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.service_factory import _resolve_provider_with_factory

    boom = RuntimeError("sk-verysecret leaked into the traceback")
    factory, _calls = _make_openai_style_factory(build_should_raise=boom)
    register_provider_factory(factory)
    store.set_secret("openai", "api_key", "sk-verysecret")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        _resolve_provider_with_factory("openai", store)
    msg = str(excinfo.value)
    assert "sk-verysecret" not in msg
    assert "Check your AI settings" in msg


# ---------------------------------------------------------------------------
# Whole-service integration
# ---------------------------------------------------------------------------

def test_adminaiservice_constructs_with_fake(store, settings):
    """get_admin_ai_service returns a service backed by the static provider."""
    from cauldron_ai.providers import register_provider
    from cauldron_ai_admin.service_factory import get_admin_ai_service

    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"include_content_tools": False},
    }
    register_provider(_FakeProvider())
    store.set_selected_provider("fake")

    service = get_admin_ai_service()
    # ``AdminAIService`` stores the resolved provider internally; the
    # attribute name is private but stable across Phase 2.
    resolved = getattr(service, "_provider", None) or getattr(
        service, "provider", None,
    )
    assert resolved is not None
    assert getattr(resolved, "name", "") == "fake"
