"""Regression tests: settings page must never show 'Active' when the
config store is invalid.

Covers every ``AIProviderStoreError`` subclass:

* Malformed JSON
* Unsupported schema version
* Symlinked config path
* Non-regular file at the config path (directory)
* Oversized config file (> 64 KB)

Also verifies:

* CAULDRON_MODULES is NOT used as a status fallback in these cases.
* The provider factory ``build()`` is never invoked while the store is
  invalid (settings page must stay off the vendor SDK path).
* No filesystem path, secret, or raw exception message leaks into HTML.
* POST actions (``save``, ``save_runtime``, ``select_provider``,
  ``clear_credential``) refuse to write and redirect safely.
"""
from __future__ import annotations

import json
import os

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from cauldron_ai.testing import reset_provider_registry_for_tests

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registry():
    reset_provider_registry_for_tests()
    yield
    reset_provider_registry_for_tests()


def _make_user(username, perms=()):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    user.set_password("pw")
    user.save()
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _settings_user():
    return _make_user(
        "settings-corrupt",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )


def _logged_in_client():
    user = _settings_user()
    client = Client()
    client.force_login(user)
    return client


def _make_factory(name: str = "corrupt-testprovider"):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )

    class _Factory:
        def __init__(self, n):
            self.name = n

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name=self.name,
                display_name=self.name.title(),
                fields=(
                    AIProviderConfigurationField(
                        name="model", label="Model",
                    ),
                    AIProviderConfigurationField(
                        name="api_key", label="API Key",
                        field_type=FIELD_TYPE_PASSWORD, required=False,
                    ),
                ),
                supports_connection_test=True,
            )

        def build(self, config, secrets):  # pragma: no cover - must not run
            raise RuntimeError("would have called vendor SDK")

        def test_connection(self, config, secrets):
            return AIProviderConnectionResult(
                success=True, status="ok", message="Test OK",
            )

    return _Factory(name)


# ---------------------------------------------------------------------------
# Store fixtures — each writes a specifically-invalid config file so the
# next ``get_store().load()`` raises the corresponding subclass of
# ``AIProviderStoreError``.
# ---------------------------------------------------------------------------


@pytest.fixture
def corrupt_store(tmp_path, settings):
    """Config file with malformed JSON → AIProviderStoreCorruptError."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    p = tmp_path / "ai.json"
    p.write_text("{not valid json", encoding="utf-8")
    os.chmod(str(p), 0o600)
    _reset_store_for_tests(path=p)
    yield p
    _reset_store_for_tests(path=None)


@pytest.fixture
def version_store(tmp_path, settings):
    """Config file with unsupported version → AIProviderStoreVersionError."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    p = tmp_path / "ai.json"
    p.write_text(json.dumps({"version": 99}), encoding="utf-8")
    os.chmod(str(p), 0o600)
    _reset_store_for_tests(path=p)
    yield p
    _reset_store_for_tests(path=None)


@pytest.fixture
def symlink_store(tmp_path, settings):
    """Config path is a symlink → AIProviderStoreUnsafePathError."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    os.chmod(str(real), 0o600)
    link = tmp_path / "ai.json"
    link.symlink_to(real)
    _reset_store_for_tests(path=link)
    yield link
    _reset_store_for_tests(path=None)


@pytest.fixture
def oversized_store(tmp_path, settings):
    """Config file larger than 64 KB → AIProviderStoreCorruptError."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    p = tmp_path / "ai.json"
    p.write_bytes(b"x" * (65 * 1024))
    os.chmod(str(p), 0o600)
    _reset_store_for_tests(path=p)
    yield p
    _reset_store_for_tests(path=None)


@pytest.fixture
def non_regular_store(tmp_path, settings):
    """Config path is a directory → AIProviderStoreUnsafePathError."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    d = tmp_path / "ai.json"
    d.mkdir()
    _reset_store_for_tests(path=d)
    yield d
    _reset_store_for_tests(path=None)


# ---------------------------------------------------------------------------
# GET: the "Configuration store invalid" status must appear and "Active"
# must never leak, regardless of CAULDRON_MODULES contents.
# ---------------------------------------------------------------------------


def test_corrupt_json_shows_store_invalid_status(corrupt_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Unknown" in response.content


def test_corrupt_json_provider_not_shown_as_active(corrupt_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Active" not in response.content


def test_unsupported_version_shows_store_invalid(version_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Active" not in response.content


def test_symlink_shows_store_invalid(symlink_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Active" not in response.content


def test_oversized_shows_store_invalid(oversized_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Active" not in response.content


def test_non_regular_file_shows_store_invalid(non_regular_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Active" not in response.content


def test_cauldron_modules_fallback_not_used_when_store_invalid(
    corrupt_store, settings,
):
    """Even with a valid CAULDRON_MODULES provider selection, the corrupt
    store status wins — we must never mislead the operator into thinking
    the provider is Active when the store cannot be read.
    """
    from cauldron_ai.providers import register_provider

    class _Fake:
        name = "fake-static"
        display_name = "Fake Static"

        def complete(self, req):  # pragma: no cover
            return None

    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"provider": "fake-static"},
    }
    register_provider(_Fake())
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Configuration store invalid" in response.content
    assert b"Active" not in response.content
    # Even the fallback provider name must not surface as the current one.
    assert b"Fake Static" not in response.content


# ---------------------------------------------------------------------------
# GET: the settings page must not run the vendor SDK or factory.build()
# when the store is broken.
# ---------------------------------------------------------------------------


def test_corrupt_store_does_not_build_factory(corrupt_store, settings):
    """A GET on a corrupt store must not invoke factory.build() — the
    settings page has to stay off the vendor SDK path.
    """
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    factory = _make_factory("hazardous-corrupt")
    register_provider_factory(factory)

    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    # If build() had run, the raise inside _make_factory would explode
    # (500) — reaching 200 already proves it wasn't called, but assert
    # the visible marker too.
    assert b"Configuration store invalid" in response.content


def test_corrupt_store_no_vendor_sdk_call(
    corrupt_store, settings, monkeypatch,
):
    """A GET must not touch the openai vendor SDK when the store is invalid."""
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    try:
        import openai  # type: ignore
    except Exception:  # pragma: no cover - openai not installed in this env
        pytest.skip("openai SDK not installed; nothing to patch")
    called = [0]

    def _boom(*args, **kwargs):
        called[0] += 1
        raise RuntimeError("openai.OpenAI() called during GET")

    monkeypatch.setattr(openai, "OpenAI", _boom, raising=False)
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert called[0] == 0


# ---------------------------------------------------------------------------
# GET: no path / secret / raw error text leaks into the page HTML.
# ---------------------------------------------------------------------------


def test_no_path_or_secret_in_html(corrupt_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    body = response.content.decode()

    # The tmp_path itself must not appear anywhere in the response.
    assert str(corrupt_store) not in body
    assert str(corrupt_store.parent) not in body
    # The literal filename we chose for the test store must not appear.
    assert "ai.json" not in body
    # The malformed content on disk must not leak.
    assert "{not valid json" not in body
    # Raw exception phrasing from the store layer must not appear.
    assert "AIProviderStoreCorruptError" not in body
    assert "malformed JSON" not in body


def test_no_raw_exception_message_for_version_error(version_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    response = _logged_in_client().get(reverse("cauldron_ai_admin:settings"))
    body = response.content.decode()
    assert "AIProviderStoreVersionError" not in body
    # The raw "version 99 is not supported" phrasing must not leak.
    assert "version 99" not in body


# ---------------------------------------------------------------------------
# POST: every write path refuses to touch the store and redirects safely.
# ---------------------------------------------------------------------------


def test_post_save_refused_when_store_invalid(corrupt_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    client = _logged_in_client()
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model": "gpt-4o", "api_key": "sk-x"},
    )
    # Must safely redirect (PRG); no 500.
    assert response.status_code == 302
    # File contents must be untouched.
    assert corrupt_store.read_text(encoding="utf-8") == "{not valid json"


def test_post_runtime_refused_when_store_invalid(corrupt_store, settings):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    client = _logged_in_client()
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={
            "action": "save_runtime",
            "max_model_turns": "4",
            "max_tool_calls": "8",
            "tool_timeout_seconds": "20",
            "run_timeout_seconds": "60",
            "max_argument_bytes": "16384",
            "max_result_bytes": "32768",
            "include_content_tools": "on",
        },
    )
    assert response.status_code == 302
    assert corrupt_store.read_text(encoding="utf-8") == "{not valid json"


def test_post_select_provider_refused_when_store_invalid(
    corrupt_store, settings,
):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    register_provider_factory(_make_factory("alpha"))

    client = _logged_in_client()
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "select_provider", "provider": "alpha"},
    )
    assert response.status_code == 302
    assert corrupt_store.read_text(encoding="utf-8") == "{not valid json"


def test_post_clear_credential_refused_when_store_invalid(
    corrupt_store, settings,
):
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {}}
    client = _logged_in_client()
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "clear_credential", "field": "api_key"},
    )
    assert response.status_code == 302
    assert corrupt_store.read_text(encoding="utf-8") == "{not valid json"
