"""Tests for AIProviderSettingsStore (Phase 2 hardening)."""
from __future__ import annotations

import json
import os
import stat
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
    doc = store.load()
    # Normalised empty document.
    assert doc["version"] == 1
    assert doc["selected_provider"] == ""
    assert doc["providers"] == {}


def test_save_and_load_roundtrip_versioned_document(tmp_path):
    store = _make_store(tmp_path)
    store.save({
        "version": 1,
        "selected_provider": "openai",
        "runtime": {"max_model_turns": 4},
        "providers": {
            "openai": {
                "config": {"model": "gpt-4o"},
                "secrets": {"api_key": "sk-abc"},
            },
        },
    })
    data = store.load()
    assert data["version"] == 1
    assert data["selected_provider"] == "openai"
    assert data["runtime"]["max_model_turns"] == 4
    assert data["providers"]["openai"]["config"]["model"] == "gpt-4o"
    assert data["providers"]["openai"]["secrets"]["api_key"] == "sk-abc"


def test_file_is_created_with_0600(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600


def test_parent_dir_is_created_with_0700(tmp_path):
    nested = tmp_path / "nested" / "ai_config.json"
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    store = AIProviderSettingsStore(nested)
    store.set_selected_provider("openai")
    mode = stat.S_IMODE(store.path.parent.stat().st_mode)
    assert mode == 0o700


def test_file_contents_are_valid_json(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    text = store.path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["version"] == 1


def test_repr_does_not_leak_path(tmp_path):
    store = _make_store(tmp_path)
    assert str(tmp_path) not in repr(store)
    assert "AIProviderSettingsStore" in repr(store)


# ---------------------------------------------------------------------------
# Migration from old format
# ---------------------------------------------------------------------------

def test_migration_from_old_format(tmp_path):
    """The pre-Phase-2 layout is migrated silently on read."""
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    p = tmp_path / "old.json"
    # Write directly to disk in the old (pre-Phase-2) format.
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "provider": "openai",
        "config": {"openai": {"model": "gpt-4o"}},
        "secrets": {"openai": {"api_key": "sk-existing"}},
    }), encoding="utf-8")
    os.chmod(str(p), 0o600)

    store = AIProviderSettingsStore(p)
    assert store.get_selected_provider() == "openai"
    assert store.get_config("openai")["model"] == "gpt-4o"
    assert store.get_secret("openai", "api_key") == "sk-existing"


def test_migration_preserves_credentials(tmp_path):
    """Credentials must not be dropped during migration."""
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "provider": "openai",
        "config": {"openai": {"model": "gpt-4o"}},
        "secrets": {"openai": {"api_key": "critical-secret"}},
    }), encoding="utf-8")
    os.chmod(str(p), 0o600)

    store = AIProviderSettingsStore(p)
    # First round-trip should upgrade the on-disk representation without
    # losing the secret.
    store.set_selected_provider("openai")
    fresh = AIProviderSettingsStore(p)
    assert fresh.get_secret("openai", "api_key") == "critical-secret"


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def test_symlink_rejected(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreUnsafePathError,
    )
    real = tmp_path / "real.json"
    real.write_text('{"version": 1}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    store = AIProviderSettingsStore(link)
    with pytest.raises(AIProviderStoreUnsafePathError):
        store.load()


def test_non_regular_file_rejected(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreUnsafePathError,
    )
    subdir = tmp_path / "dir"
    subdir.mkdir()
    store = AIProviderSettingsStore(subdir)
    with pytest.raises(AIProviderStoreUnsafePathError):
        store.load()


def test_oversized_file_rejected(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreCorruptError,
    )
    p = tmp_path / "big.json"
    p.write_text("{" + "a" * (65 * 1024) + "}", encoding="utf-8")
    os.chmod(str(p), 0o600)
    store = AIProviderSettingsStore(p)
    with pytest.raises(AIProviderStoreCorruptError):
        store.load()


def test_malformed_json_raises_corrupt(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreCorruptError,
    )
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    os.chmod(str(p), 0o600)
    store = AIProviderSettingsStore(p)
    with pytest.raises(AIProviderStoreCorruptError):
        store.load()


def test_non_object_json_raises_corrupt(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreCorruptError,
    )
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    os.chmod(str(p), 0o600)
    store = AIProviderSettingsStore(p)
    with pytest.raises(AIProviderStoreCorruptError):
        store.load()


def test_unsupported_version_raises(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        AIProviderStoreVersionError,
    )
    p = tmp_path / "v2.json"
    p.write_text('{"version": 99}', encoding="utf-8")
    os.chmod(str(p), 0o600)
    store = AIProviderSettingsStore(p)
    with pytest.raises(AIProviderStoreVersionError):
        store.load()


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
    store.set_config("openai", {"model": "gpt-4o-mini"})
    cfg = store.get_config("openai")
    assert cfg["model"] == "gpt-4o-mini"


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


def test_clear_secret_removes_only_named_key(tmp_path):
    store = _make_store(tmp_path)
    store.set_secret("openai", "api_key", "sk-test")
    store.set_secret("openai", "other", "keep")
    store.clear_secret("openai", "api_key")
    assert store.get_secret("openai", "api_key") == ""
    assert store.get_secret("openai", "other") == "keep"


def test_clear_secret_missing_key_is_noop(tmp_path):
    store = _make_store(tmp_path)
    # Should not raise, and should not create the provider slot.
    store.clear_secret("openai", "not_there")
    assert store.get_secrets("openai") == {}


def test_clear_secret_unknown_provider_is_noop(tmp_path):
    store = _make_store(tmp_path)
    store.clear_secret("unknown", "anything")


# ---------------------------------------------------------------------------
# Runtime settings
# ---------------------------------------------------------------------------

def test_get_runtime_empty_by_default(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_runtime() == {}


def test_set_and_get_runtime(tmp_path):
    store = _make_store(tmp_path)
    store.set_runtime({"max_model_turns": 4, "tool_timeout_seconds": 15.0})
    runtime = store.get_runtime()
    assert runtime["max_model_turns"] == 4
    assert runtime["tool_timeout_seconds"] == 15.0


def test_set_runtime_persists(tmp_path):
    store = _make_store(tmp_path)
    store.set_runtime({"max_model_turns": 4})
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    store2 = AIProviderSettingsStore(store.path)
    assert store2.get_runtime()["max_model_turns"] == 4


# ---------------------------------------------------------------------------
# Atomic write / concurrency
# ---------------------------------------------------------------------------

def test_write_uses_unique_temp_file(tmp_path, monkeypatch):
    """Writes never collide even if two calls race to create the temp file."""
    store = _make_store(tmp_path)

    seen_names: list[str] = []
    real_open = os.open

    def _wrapper(path, flags, mode=0o777):
        if isinstance(path, str) and ".cauldron_ai_config_" in path:
            seen_names.append(path)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _wrapper)
    store.set_selected_provider("a")
    store.set_selected_provider("b")
    store.set_selected_provider("c")
    # All three writes should have used distinct temp file names.
    assert len(seen_names) == len(set(seen_names)) == 3


def test_failed_write_preserves_original(tmp_path, monkeypatch):
    """A crash mid-write must not corrupt or truncate the existing file."""
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    original_text = store.path.read_text(encoding="utf-8")

    real_replace = os.replace

    def _failing_replace(src, dst):  # pragma: no cover - path exercised by test
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(OSError):
        store.set_selected_provider("anthropic")
    monkeypatch.setattr(os, "replace", real_replace)
    assert store.path.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# File existence and permissions
# ---------------------------------------------------------------------------

def test_file_exists_false_before_save(tmp_path):
    store = _make_store(tmp_path)
    assert not store.file_exists()


def test_file_exists_true_after_save(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    assert store.file_exists()


def test_file_permissions_ok_after_save(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    assert store.file_permissions_ok()


def test_file_permissions_ok_false_when_wrong_mode(tmp_path):
    store = _make_store(tmp_path)
    store.set_selected_provider("openai")
    os.chmod(str(store.path), 0o644)
    assert not store.file_permissions_ok()


def test_file_permissions_ok_false_when_no_file(tmp_path):
    store = _make_store(tmp_path)
    assert not store.file_permissions_ok()


def test_parent_permissions_ok_after_save(tmp_path):
    nested = tmp_path / "sub" / "ai.json"
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    store = AIProviderSettingsStore(nested)
    store.set_selected_provider("openai")
    assert store.parent_permissions_ok()


def test_parent_permissions_ok_false_when_open(tmp_path):
    nested = tmp_path / "open_dir" / "ai.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(nested.parent), 0o755)
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore
    store = AIProviderSettingsStore(nested)
    # Do not save (would tighten the mode); check the current mode.
    assert not store.parent_permissions_ok()


# ---------------------------------------------------------------------------
# resolve_provider_name helper
# ---------------------------------------------------------------------------

def test_resolve_provider_name_from_store(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_name,
    )
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_selected_provider("openai")
    assert resolve_provider_name(store) == "openai"


def test_resolve_provider_name_from_modules_fallback(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_name,
    )
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {"provider": "fake"}}
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    assert resolve_provider_name(store) == "fake"


def test_resolve_provider_name_store_takes_precedence(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_name,
    )
    settings.CAULDRON_MODULES = {"cauldron.ai.admin": {"provider": "fake"}}
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_selected_provider("openai")
    assert resolve_provider_name(store) == "openai"


def test_resolve_provider_name_empty_when_neither_set(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_name,
    )
    settings.CAULDRON_MODULES = {}
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    assert resolve_provider_name(store) == ""


# ---------------------------------------------------------------------------
# resolve_provider_config helper
# ---------------------------------------------------------------------------

def test_resolve_provider_config_file_overrides_modules(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_config,
    )
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {
            "provider_config": {"openai": {"model": "gpt-3.5"}},
        },
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    store.set_config("openai", {"model": "gpt-4o"})
    cfg = resolve_provider_config("openai", store)
    assert cfg["model"] == "gpt-4o"


def test_resolve_provider_config_modules_used_when_no_file(tmp_path, settings):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        resolve_provider_config,
    )
    settings.CAULDRON_MODULES = {
        "cauldron.ai.admin": {
            "provider_config": {"openai": {"model": "gpt-3.5"}},
        },
    }
    store = AIProviderSettingsStore(tmp_path / "ai.json")
    cfg = resolve_provider_config("openai", store)
    assert cfg["model"] == "gpt-3.5"
