"""Tests for AIProviderSettingsStore."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest


def _make_store(tmp_path: Path):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    return AIProviderSettingsStore(tmp_path / "ai_config.json")


# ---------------------------------------------------------------------------
# Basic read / write
# ---------------------------------------------------------------------------

def test_load_empty_when_no_file(tmp_path):
    store = _make_store(tmp_path)
    assert store.load() == {}


def test_save_and_load_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    store.save({"provider": "openai", "config": {"openai": {"model_name": "gpt-4o"}}})
    data = store.load()
    assert data["provider"] == "openai"
    assert data["config"]["openai"]["model_name"] == "gpt-4o"


def test_file_is_created_with_0600(tmp_path):
    store = _make_store(tmp_path)
    store.save({"provider": "openai"})
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600


def test_file_contents_are_valid_json(tmp_path):
    store = _make_store(tmp_path)
    store.save({"provider": "openai"})
    text = store.path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["provider"] == "openai"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def test_get_selected_provider_empty_by_default(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_selected_provider() == ""


def test_set_and_get_selected_provider(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    assert store.get_selected_provider() == "openai"


def test_set_selected_provider_persists(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    store2 = AIProviderSettingsStore(store.path)
    assert store2.get_selected_provider() == "openai"


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

def test_get_config_returns_empty_when_absent(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_config("openai") == {}


def test_set_and_get_config(tmp_path):
    store = _make_store(tmp_path)
    store.set_config("openai", {"model_name": "gpt-4o-mini"})
    cfg = store.get_config("openai")
    assert cfg["model_name"] == "gpt-4o-mini"


def test_set_config_does_not_overwrite_other_providers(tmp_path):
    store = _make_store(tmp_path)
    store.set_config("openai", {"a": 1})
    store.set_config("anthropic", {"b": 2})
    assert store.get_config("openai") == {"a": 1}
    assert store.get_config("anthropic") == {"b": 2}


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def test_get_secret_returns_empty_when_absent(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_secret("openai", "api_key") == ""


def test_set_and_get_secret(tmp_path):
    store = _make_store(tmp_path)
    store.set_secret("openai", "api_key", "sk-test")
    assert store.get_secret("openai", "api_key") == "sk-test"


def test_get_secrets_returns_dict(tmp_path):
    store = _make_store(tmp_path)
    store.set_secret("openai", "api_key", "sk-test")
    store.set_secret("openai", "org", "org-123")
    s = store.get_secrets("openai")
    assert s == {"api_key": "sk-test", "org": "org-123"}


def test_set_secrets_batch(tmp_path):
    store = _make_store(tmp_path)
    store.set_secrets("openai", {"api_key": "sk-test", "org": "org-1"})
    assert store.get_secret("openai", "api_key") == "sk-test"
    assert store.get_secret("openai", "org") == "org-1"


def test_secrets_not_in_config(tmp_path):
    store = _make_store(tmp_path)
    store.set_secret("openai", "api_key", "sk-test")
    assert "api_key" not in store.get_config("openai")


# ---------------------------------------------------------------------------
# File existence and permissions
# ---------------------------------------------------------------------------

def test_file_exists_false_before_save(tmp_path):
    store = _make_store(tmp_path)
    assert not store.file_exists()


def test_file_exists_true_after_save(tmp_path):
    store = _make_store(tmp_path)
    store.save({})
    assert store.file_exists()


def test_file_permissions_ok_after_save(tmp_path):
    store = _make_store(tmp_path)
    store.save({})
    assert store.file_permissions_ok()


def test_file_permissions_ok_false_when_wrong_mode(tmp_path):
    store = _make_store(tmp_path)
    store.save({})
    os.chmod(str(store.path), 0o644)
    assert not store.file_permissions_ok()


def test_file_permissions_ok_false_when_no_file(tmp_path):
    store = _make_store(tmp_path)
    assert not store.file_permissions_ok()


# ---------------------------------------------------------------------------
# resolve_provider_name helper
# ---------------------------------------------------------------------------

def test_resolve_provider_name_from_store(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_name
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_selected_provider("openai")
    assert resolve_provider_name(store) == "openai"


def test_resolve_provider_name_from_modules_fallback(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_name
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"provider": "fake"},
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")  # no stored name
    assert resolve_provider_name(store) == "fake"


def test_resolve_provider_name_store_takes_precedence(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_name
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {"provider": "fake"},
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_selected_provider("openai")
    assert resolve_provider_name(store) == "openai"


def test_resolve_provider_name_empty_when_neither_set(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_name
    settings.CAULDRON_MODULES = {}
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    assert resolve_provider_name(store) == ""


# ---------------------------------------------------------------------------
# resolve_provider_config helper
# ---------------------------------------------------------------------------

def test_resolve_provider_config_file_overrides_modules(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_config
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {
            "provider_config": {"openai": {"model_name": "gpt-3.5"}},
        }
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_config("openai", {"model_name": "gpt-4o"})
    cfg = resolve_provider_config("openai", store)
    assert cfg["model_name"] == "gpt-4o"


def test_resolve_provider_config_modules_used_when_no_file(tmp_path, settings):
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, resolve_provider_config
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {
            "provider_config": {"openai": {"model_name": "gpt-3.5"}},
        }
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    cfg = resolve_provider_config("openai", store)
    assert cfg["model_name"] == "gpt-3.5"
