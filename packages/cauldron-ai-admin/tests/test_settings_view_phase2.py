"""Phase 2 settings view tests: provider config form, connection test, save."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from cauldron_ai.providers import _reset_registry_for_tests

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


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
                        name="model_name", label="Model"
                    ),
                    AIProviderConfigurationField(
                        name="api_key", label="API Key",
                        field_type=FIELD_TYPE_PASSWORD, required=False,
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
    assert b"model_name" in response.content


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
        data={"action": "save", "model_name": "gpt-4o-mini", "api_key": ""},
    )
    assert response.status_code == 200
    assert store.get_config("myprovider").get("model_name") == "gpt-4o-mini"


def test_settings_post_save_stores_secret_when_nonempty(store, settings):
    from cauldron_ai.providers import register_provider_factory
    settings.CAULDRON_MODULES = {}
    register_provider_factory(_make_factory("myprovider"))
    store.set_selected_provider("myprovider")
    user = _settings_user()
    client = Client()
    client.force_login(user)
    client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model_name": "gpt-4o", "api_key": "sk-new"},
    )
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
    client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "save", "model_name": "gpt-4o", "api_key": ""},
    )
    assert store.get_secret("myprovider", "api_key") == "sk-existing"


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
    client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "select_provider", "provider": "beta"},
    )
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
        data={"action": "test", "model_name": "gpt-4o", "api_key": "sk-test"},
    )
    assert response.status_code == 200
    assert b"Test OK" in response.content or b"Connected" in response.content


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
        data={"action": "test", "model_name": "gpt-4o", "api_key": "sk-test"},
    )
    # Second test — should be throttled
    response = client.post(
        reverse("cauldron_ai_admin:settings"),
        data={"action": "test", "model_name": "gpt-4o", "api_key": "sk-test"},
    )
    assert b"throttled" in response.content.lower() or b"wait" in response.content.lower()


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
