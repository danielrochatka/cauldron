"""Phase 2 settings view tests: provider config form, connection test, save."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from cauldron_ai.testing import reset_provider_registry_for_tests

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_registry():
    reset_provider_registry_for_tests()
    yield
    reset_provider_registry_for_tests()


@pytest.fixture()
def store(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, _reset_store_for_tests
    p = tmp_path / "ai.json"
    _reset_store_for_tests(path=p)
    yield AIProviderSettingsStore(p)
    _reset_store_for_tests(path=None)


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
        "settings-p2",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )


def _make_factory(name: str = "testprovider"):
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
                        name="model", label="Model"
                    ),
                    AIProviderConfigurationField(
                        name="api_key", label="API Key",
                        field_type=FIELD_TYPE_PASSWORD, required=False,
                        environment_variable="TEST_FACTORY_KEY",
                    ),
                ),
                supports_connection_test=True,
            )

        def build(self, config, secrets):
            class _P:
                pass
            p = _P()
            p.name = self.name
            p.complete = lambda r: None
            return p

        def test_connection(self, config, secrets):
            return AIProviderConnectionResult(
                success=True, status="ok", message="Test OK"
            )

    return _Factory(name)


# ---------------------------------------------------------------------------
# GET: shows available providers
# ---------------------------------------------------------------------------

def test_settings_get_shows_available_factory(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"myprovider" in response.content.lower() or b"Myprovider" in response.content


def test_settings_get_shows_config_form_when_provider_selected(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    # The dynamic form exposes a "model" input for the test factory.
    assert b'name="model"' in response.content


def test_settings_get_no_api_key_prefilled(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-secret")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert b"sk-secret" not in response.content


# ---------------------------------------------------------------------------
# POST: save config
# ---------------------------------------------------------------------------

def test_settings_post_save_stores_config(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model": "gpt-4o-mini", "api_key": ""},
    )
    # PRG — successful save returns a redirect.
    assert response.status_code == 302
    assert store.get_config("myprovider").get("model") == "gpt-4o-mini"


def test_settings_post_save_stores_secret_when_nonempty(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model": "gpt-4o", "api_key": "sk-new"},
    )
    assert response.status_code == 302
    assert store.get_secret("myprovider", "api_key") == "sk-new"


def test_settings_post_save_leaves_existing_secret_when_empty(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-existing")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model": "gpt-4o", "api_key": ""},
    )
    assert response.status_code == 302
    assert store.get_secret("myprovider", "api_key") == "sk-existing"


def test_settings_post_save_clear_credential_removes_stored_secret(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-existing")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={
            "action": "save",
            "model": "gpt-4o",
            "api_key": "",
            "clear_api_key": "on",
        },
    )
    assert response.status_code == 302
    assert store.get_secret("myprovider", "api_key") == ""


def test_settings_post_clear_credential_action(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-existing")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "clear_credential", "field": "api_key"},
    )
    assert response.status_code == 302
    assert store.get_secret("myprovider", "api_key") == ""


def test_settings_post_clear_credential_rejects_unknown_field(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-existing")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "clear_credential", "field": "something_else"},
    )
    assert response.status_code == 302
    # Secret must remain — arbitrary fields cannot be nuked.
    assert store.get_secret("myprovider", "api_key") == "sk-existing"


# ---------------------------------------------------------------------------
# POST: save runtime settings
# ---------------------------------------------------------------------------

def test_settings_post_save_runtime_stores_values(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
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
    runtime = store.get_runtime()
    assert runtime["max_model_turns"] == 4
    assert runtime["max_tool_calls"] == 8
    assert runtime["include_content_tools"] is True


def test_settings_post_save_runtime_rejects_invalid(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={
            "action": "save_runtime",
            "max_model_turns": "0",  # invalid
            "max_tool_calls": "8",
            "tool_timeout_seconds": "20",
            "run_timeout_seconds": "60",
            "max_argument_bytes": "16384",
            "max_result_bytes": "32768",
        },
    )
    # Invalid form rerenders the page instead of redirecting.
    assert response.status_code == 200
    assert store.get_runtime() == {}


# ---------------------------------------------------------------------------
# POST: select provider
# ---------------------------------------------------------------------------

def test_settings_post_select_provider(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("alpha"))
    register_provider_factory(_make_factory("beta"))
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "select_provider", "provider": "beta"},
    )
    assert response.status_code == 302
    assert store.get_selected_provider() == "beta"


# ---------------------------------------------------------------------------
# POST: connection test
# ---------------------------------------------------------------------------

def test_settings_post_test_shows_result(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "test", "model": "gpt-4o", "api_key": "sk-test"},
    )
    # test action renders directly (no redirect) so the operator sees the outcome.
    assert response.status_code == 200
    assert b"Test OK" in response.content or b"Connected" in response.content


def test_settings_post_test_does_not_save_config(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "test", "model": "gpt-4o", "api_key": "sk-transient"},
    )
    # Neither config nor secrets should have been persisted by the test action.
    assert store.get_config("myprovider") == {}
    assert store.get_secret("myprovider", "api_key") == ""


def test_settings_post_test_throttled_on_second_call(store, settings):
    from django.core.cache import cache
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    # First test — sets throttle cache
    client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "test", "model": "gpt-4o", "api_key": "sk-test"},
    )
    # Second test — should be throttled
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "test", "model": "gpt-4o", "api_key": "sk-test"},
    )
    assert (
        b"throttled" in response.content.lower()
        or b"wait" in response.content.lower()
    )


# ---------------------------------------------------------------------------
# GET does not exercise the vendor SDK (safety)
# ---------------------------------------------------------------------------

def test_settings_get_does_not_call_factory_build(store, settings):
    """A GET must not invoke build() or test_connection() on the factory."""
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )
    from cauldron_ai.providers import register_provider_factory
    build_count = [0]
    test_count = [0]

    class _F:
        name = "hazardous"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="hazardous",
                display_name="Hazardous",
                fields=(
                    AIProviderConfigurationField(
                        name="model", label="Model",
                    ),
                    AIProviderConfigurationField(
                        name="api_key", label="Key",
                        field_type=FIELD_TYPE_PASSWORD,
                    ),
                ),
                supports_connection_test=True,
            )

        def build(self, c, s):
            build_count[0] += 1
            raise RuntimeError("would have called vendor SDK")

        def test_connection(self, c, s):
            test_count[0] += 1
            return AIProviderConnectionResult(success=True, status="ok")

    settings.CAULDRON_MODULES = {}
    register_provider_factory(_F())
    store.set_selected_provider("hazardous")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert build_count[0] == 0
    assert test_count[0] == 0


def test_settings_page_shows_credential_state(store, settings, monkeypatch):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    store.set_secret("myprovider", "api_key", "sk-stored")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    content = response.content.decode()
    assert "Configured in managed storage" in content
    # Stored secret value must never appear on the page.
    assert "sk-stored" not in content


# ---------------------------------------------------------------------------
# Existing phase-1 tests still pass
# ---------------------------------------------------------------------------

def test_settings_url_reverses():
    url = reverse("cauldron_ai_admin:settings")
    assert url == "/admin/ai/settings/"


def test_settings_unauthenticated_redirects():
    client = Client()
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code in (302, 401, 403)


def test_settings_no_permission_returns_403():
    user = _make_user("noperm-p2")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 403


def test_settings_page_shows_module_slug():
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert b"cauldron.ai.admin" in response.content


# ---------------------------------------------------------------------------
# Effective-provider display header
# ---------------------------------------------------------------------------

class _StaticFake:
    name = "fake-static"
    display_name = "Fake Static"

    def complete(self, req):  # pragma: no cover
        return None


def test_display_shows_saved_selection_over_cauldron_modules(store, settings):
    """Store selection wins over CAULDRON_MODULES when both point at a provider."""
    from cauldron_ai.providers import register_provider, register_provider_factory
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"provider": "fake-static"},
    }
    register_provider(_StaticFake())
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    # Header must reflect the STORE selection, not CAULDRON_MODULES.
    assert b"Myprovider" in response.content
    assert b"Active" in response.content


def test_display_falls_back_to_cauldron_modules_when_store_empty(store, settings):
    """Without a store selection the display uses CAULDRON_MODULES."""
    from cauldron_ai.providers import register_provider
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"provider": "fake-static"},
    }
    register_provider(_StaticFake())
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"Fake Static" in response.content
    assert b"Active" in response.content


def test_display_handles_unknown_saved_provider(store, settings):
    """A stale store selection surfaces a safe warning without crashing."""
    settings.CAULDRON_MODULES = {}
    store.set_selected_provider("nonexistent")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"nonexistent" in response.content
    assert b"Provider not found" in response.content


def test_display_handles_corrupt_store(tmp_path, settings):
    """A corrupt config file must not break the settings page."""
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    p = tmp_path / "ai.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not-json", encoding="utf-8")
    import os
    os.chmod(str(p), 0o600)
    _reset_store_for_tests(path=p)
    try:
        settings.CAULDRON_MODULES = {}
        user = _settings_user()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron_ai_admin:settings"))
        # The page renders — the header may report an opaque status but the
        # user isn't blocked from re-configuring.
        assert response.status_code == 200
    finally:
        _reset_store_for_tests(path=None)


def test_display_get_does_not_build_provider(store, settings):
    """A GET on the settings page must not invoke factory.build()."""
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )
    from cauldron_ai.providers import register_provider_factory
    build_count = [0]

    class _F:
        name = "no-build"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="no-build",
                display_name="No Build",
                fields=(
                    AIProviderConfigurationField(name="model", label="Model"),
                    AIProviderConfigurationField(
                        name="api_key", label="Key",
                        field_type=FIELD_TYPE_PASSWORD,
                    ),
                ),
                supports_connection_test=True,
            )

        def build(self, c, s):
            build_count[0] += 1
            raise RuntimeError("would have called vendor SDK")

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    settings.CAULDRON_MODULES = {}
    register_provider_factory(_F())
    store.set_selected_provider("no-build")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"No Build" in response.content
    assert build_count[0] == 0


def test_display_shows_factory_display_name_from_spec(store, settings):
    """Factory-only providers surface via ``descriptor_for`` synthesis."""
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("openai"))
    store.set_selected_provider("openai")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    # ``_make_factory("openai")`` reports display_name="Openai" (title-cased).
    assert b"Openai" in response.content
    assert b"Active" in response.content
