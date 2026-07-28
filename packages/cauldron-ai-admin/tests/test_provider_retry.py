"""Provider error classification, retry logic, and pre-call deadline tests."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from cauldron_ai.contracts import AIModelResponse
from cauldron_ai.provider_configuration import (
    AIProviderAuthenticationError,
    AIProviderConnectionError,
    AIProviderRateLimitError,
    AIProviderResponseError,
)
from cauldron_ai_admin.service import (
    AdminAIService,
    _build_provider_error_summary,
    _classify_provider_error,
)
from cauldron_ai_admin.tools import AdminAIToolRegistry
from helpers import make_assembly_service_for_tools

pytestmark = pytest.mark.django_db

_GOOD_RESPONSE = AIModelResponse(
    provider_request_id="r1",
    content="Done.",
    stop_reason="end_turn",
)


def _mock_provider(*side_effects):
    p = mock.MagicMock()
    p.name = "fake"
    p.model = "fake"
    p.complete.side_effect = list(side_effects)
    return p


def _service(provider, *, run_timeout_seconds=30):
    asm = make_assembly_service_for_tools()
    return AdminAIService(
        provider=provider,
        tool_registry=AdminAIToolRegistry(),
        run_timeout_seconds=run_timeout_seconds,
        prompt_assembly_service=asm,
    )


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="pr-retry-user")
    perm = Permission.objects.get(
        codename="use_admin_ai",
        content_type__app_label="cauldron_ai_admin",
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# classify_provider_error unit tests (no DB needed but pytestmark is fine)
# ---------------------------------------------------------------------------

def test_classify_connection_error_is_retryable():
    code, retryable = _classify_provider_error(AIProviderConnectionError("net fail"))
    assert code == "provider.connection_error"
    assert retryable is True


def test_classify_rate_limit_error_is_retryable():
    code, retryable = _classify_provider_error(AIProviderRateLimitError("429"))
    assert code == "provider.rate_limited"
    assert retryable is True


def test_classify_auth_error_is_not_retryable():
    code, retryable = _classify_provider_error(AIProviderAuthenticationError("bad key"))
    assert code == "provider.authentication_error"
    assert retryable is False


def test_classify_response_error_with_5xx_is_server_error_and_retryable():
    exc = AIProviderResponseError("server boom")
    exc.http_status = 503
    code, retryable = _classify_provider_error(exc)
    assert code == "provider.server_error"
    assert retryable is True


def test_classify_response_error_with_429_is_rate_limited_and_retryable():
    exc = AIProviderResponseError("rate limit")
    exc.http_status = 429
    code, retryable = _classify_provider_error(exc)
    assert code == "provider.rate_limited"
    assert retryable is True


def test_classify_response_error_without_status_is_invalid_request_not_retryable():
    code, retryable = _classify_provider_error(AIProviderResponseError("bad body"))
    assert code == "provider.invalid_request"
    assert retryable is False


# ---------------------------------------------------------------------------
# Pre-call deadline check (integration)
# ---------------------------------------------------------------------------

def test_pre_call_deadline_too_short_produces_provider_timeout():
    """When remaining time < _MIN_PROVIDER_CALL_DEADLINE, fail before calling provider."""
    provider = _mock_provider()
    svc = _service(provider, run_timeout_seconds=3)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.timeout"
    assert provider.complete.call_count == 0


# ---------------------------------------------------------------------------
# Retry behaviour (integration)
# ---------------------------------------------------------------------------

def test_connection_error_produces_classified_error_code():
    provider = _mock_provider(AIProviderConnectionError("net fail"), AIProviderConnectionError("net fail again"))
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"


def test_auth_error_is_not_retried():
    """Authentication failures must not be retried — retrying cannot fix bad credentials."""
    provider = _mock_provider(AIProviderAuthenticationError("bad key"))
    svc = _service(provider)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.authentication_error"
    assert provider.complete.call_count == 1


def test_connection_error_is_retried_once_when_deadline_allows():
    """A retryable error triggers exactly one retry when the deadline allows."""
    provider = _mock_provider(
        AIProviderConnectionError("net fail"),
        AIProviderConnectionError("net fail again"),
    )
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"
    assert provider.complete.call_count == 2


def test_connection_error_not_retried_when_deadline_too_close():
    """When retrying would leave too little time, skip the retry."""
    provider = _mock_provider(AIProviderConnectionError("net fail"))
    svc = _service(provider)
    user = _make_user()

    # Make the retry backoff enormous so the remaining deadline check blocks retry.
    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 9999):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"
    assert provider.complete.call_count == 1


def test_retry_succeeds_on_second_attempt():
    """If the first call raises a retryable error but the second succeeds, run completes."""
    provider = _mock_provider(AIProviderConnectionError("transient"), _GOOD_RESPONSE)
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "completed"
    assert provider.complete.call_count == 2


# ---------------------------------------------------------------------------
# Diagnostic summary (integration)
# ---------------------------------------------------------------------------

def test_error_summary_contains_safe_diagnostic_json():
    """error_summary must be parseable JSON with all required diagnostic fields."""
    provider = _mock_provider(AIProviderConnectionError("net fail"), AIProviderConnectionError("again"))
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.error_code == "provider.connection_error"
    data = json.loads(run.error_summary)
    assert data["error_code"] == "provider.connection_error"
    assert data["exc_class"] == "AIProviderConnectionError"
    assert data["turn"] == 0
    assert isinstance(data["elapsed_ms"], int)
    assert isinstance(data["remaining_ms"], int)
    assert data["retryable"] is True
