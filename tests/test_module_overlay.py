"""Tests for the module-state overlay (cauldron.modules.overlay)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cauldron.modules.overlay import apply_overlay, load_overlay, save_overlay


# ---------------------------------------------------------------------------
# load_overlay
# ---------------------------------------------------------------------------

class TestLoadOverlay:
    def test_missing_file_returns_empty_no_warning(self, tmp_path):
        """load_overlay returns ({}, None) when the file is absent."""
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {}
        assert warning is None

    def test_valid_file_returns_parsed_overrides(self, tmp_path):
        """load_overlay parses a well-formed overlay file correctly."""
        data = {
            "version": 1,
            "overrides": {
                "cauldron.content": {"enabled": False},
                "cauldron.auth": {"enabled": True},
            },
        }
        (tmp_path / "module_state.json").write_text(json.dumps(data), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {
            "cauldron.content": {"enabled": False},
            "cauldron.auth": {"enabled": True},
        }
        assert warning is None

    def test_malformed_json_returns_warning(self, tmp_path):
        """load_overlay returns ({}, warning) for unparseable JSON."""
        (tmp_path / "module_state.json").write_text("{not valid json", encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {}
        assert warning is not None
        assert "could not be read" in warning

    def test_wrong_version_returns_warning(self, tmp_path):
        """load_overlay returns ({}, warning) for an unsupported version."""
        data = {"version": 99, "overrides": {}}
        (tmp_path / "module_state.json").write_text(json.dumps(data), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {}
        assert warning is not None
        assert "unsupported version" in warning

    def test_non_bool_enabled_returns_warning(self, tmp_path):
        """load_overlay skips entries where 'enabled' is not a bool."""
        data = {
            "version": 1,
            "overrides": {
                "cauldron.content": {"enabled": "yes"},  # string, not bool
            },
        }
        (tmp_path / "module_state.json").write_text(json.dumps(data), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        # The malformed entry is skipped
        assert overrides == {}
        assert warning is not None
        assert "cauldron.content" in warning

    def test_root_not_object_returns_warning(self, tmp_path):
        """load_overlay returns ({}, warning) when root is not a JSON object."""
        (tmp_path / "module_state.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {}
        assert warning is not None
        assert "root must be a JSON object" in warning

    def test_overrides_not_object_returns_warning(self, tmp_path):
        """load_overlay returns ({}, warning) when 'overrides' is not an object."""
        data = {"version": 1, "overrides": ["list"]}
        (tmp_path / "module_state.json").write_text(json.dumps(data), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert overrides == {}
        assert warning is not None

    def test_valid_entries_returned_skipping_malformed(self, tmp_path):
        """Valid entries are returned even when some entries are malformed."""
        data = {
            "version": 1,
            "overrides": {
                "cauldron.good": {"enabled": True},
                "cauldron.bad": {"enabled": "not-a-bool"},
            },
        }
        (tmp_path / "module_state.json").write_text(json.dumps(data), encoding="utf-8")
        overrides, warning = load_overlay(tmp_path)
        assert "cauldron.good" in overrides
        assert "cauldron.bad" not in overrides
        assert warning is not None


# ---------------------------------------------------------------------------
# save_overlay
# ---------------------------------------------------------------------------

class TestSaveOverlay:
    def test_writes_file_atomically(self, tmp_path):
        """save_overlay produces the expected file content and no temp files remain."""
        overrides = {"cauldron.content": {"enabled": False}}
        save_overlay(tmp_path, overrides)

        target = tmp_path / "module_state.json"
        assert target.exists()

        # No leftover temp files.
        tmp_files = [f for f in tmp_path.iterdir() if f.name != "module_state.json"]
        assert tmp_files == []

        # Content round-trips correctly.
        loaded, warning = load_overlay(tmp_path)
        assert loaded == overrides
        assert warning is None

    def test_save_creates_parent_dir(self, tmp_path):
        """save_overlay creates the data directory if it does not exist."""
        data_dir = tmp_path / "data" / "subdir"
        assert not data_dir.exists()
        save_overlay(data_dir, {"cauldron.x": {"enabled": True}})
        assert (data_dir / "module_state.json").exists()

    def test_raises_value_error_for_invalid_slug(self, tmp_path):
        """save_overlay raises ValueError for an empty or non-string slug."""
        with pytest.raises(ValueError, match="Invalid slug"):
            save_overlay(tmp_path, {"": {"enabled": True}})

    def test_raises_value_error_for_missing_enabled(self, tmp_path):
        """save_overlay raises ValueError when 'enabled' key is missing."""
        with pytest.raises(ValueError, match="must have 'enabled'"):
            save_overlay(tmp_path, {"cauldron.x": {}})

    def test_raises_value_error_for_non_bool_enabled(self, tmp_path):
        """save_overlay raises ValueError when 'enabled' is not a bool."""
        with pytest.raises(ValueError, match="must have 'enabled'"):
            save_overlay(tmp_path, {"cauldron.x": {"enabled": 1}})

    def test_overwrites_existing_file(self, tmp_path):
        """save_overlay replaces an existing overlay file."""
        save_overlay(tmp_path, {"cauldron.a": {"enabled": True}})
        save_overlay(tmp_path, {"cauldron.b": {"enabled": False}})
        loaded, _ = load_overlay(tmp_path)
        assert loaded == {"cauldron.b": {"enabled": False}}


# ---------------------------------------------------------------------------
# apply_overlay
# ---------------------------------------------------------------------------

class TestApplyOverlay:
    def test_disable_removes_module(self):
        """apply_overlay with enabled=False removes the slug from the result."""
        modules = {"cauldron.content": {"routing": {}}, "cauldron.auth": {}}
        result = apply_overlay(modules, {"cauldron.content": {"enabled": False}})
        assert "cauldron.content" not in result
        assert "cauldron.auth" in result

    def test_enable_adds_missing_module(self):
        """apply_overlay with enabled=True adds a slug not already in the dict."""
        modules = {"cauldron.auth": {}}
        result = apply_overlay(modules, {"cauldron.new": {"enabled": True}})
        assert "cauldron.new" in result
        assert result["cauldron.new"] == {}

    def test_enable_preserves_existing_config(self):
        """apply_overlay with enabled=True does not overwrite existing module config."""
        modules = {"cauldron.content": {"routing": {"default_provider": "flatfile"}}}
        result = apply_overlay(modules, {"cauldron.content": {"enabled": True}})
        assert result["cauldron.content"] == {"routing": {"default_provider": "flatfile"}}

    def test_preserves_other_modules_unchanged(self):
        """apply_overlay does not touch modules not mentioned in overrides."""
        modules = {
            "cauldron.content": {},
            "cauldron.auth": {},
            "cauldron.admin": {},
        }
        result = apply_overlay(modules, {"cauldron.content": {"enabled": False}})
        assert "cauldron.auth" in result
        assert "cauldron.admin" in result

    def test_original_dict_not_mutated(self):
        """apply_overlay never mutates the input module_settings dict."""
        modules = {"cauldron.content": {}, "cauldron.auth": {}}
        original_keys = set(modules.keys())
        apply_overlay(modules, {"cauldron.content": {"enabled": False}})
        assert set(modules.keys()) == original_keys

    def test_empty_overrides_returns_copy(self):
        """apply_overlay with no overrides returns an equal but distinct dict."""
        modules = {"cauldron.content": {}}
        result = apply_overlay(modules, {})
        assert result == modules
        assert result is not modules

    def test_restart_simulation_disable_then_enable(self):
        """Simulate a restart cycle: disable removes the module, re-enable adds it back."""
        modules = {"cauldron.content": {"routing": {}}, "cauldron.auth": {}}

        # First startup: disable cauldron.content
        after_disable = apply_overlay(modules, {"cauldron.content": {"enabled": False}})
        assert "cauldron.content" not in after_disable

        # Second startup: re-enable cauldron.content
        after_enable = apply_overlay(modules, {"cauldron.content": {"enabled": True}})
        assert "cauldron.content" in after_enable
        # Original config is restored from base modules dict
        assert after_enable["cauldron.content"] == {"routing": {}}

    def test_disable_nonexistent_module_is_noop(self):
        """Disabling a slug that is not in the dict is a no-op."""
        modules = {"cauldron.auth": {}}
        result = apply_overlay(modules, {"cauldron.ghost": {"enabled": False}})
        assert result == modules

    def test_multiple_overrides_applied(self):
        """apply_overlay handles multiple overrides in one call."""
        modules = {
            "cauldron.a": {},
            "cauldron.b": {},
            "cauldron.c": {},
        }
        result = apply_overlay(
            modules,
            {
                "cauldron.a": {"enabled": False},
                "cauldron.b": {"enabled": True},
                "cauldron.d": {"enabled": True},
            },
        )
        assert "cauldron.a" not in result
        assert "cauldron.b" in result
        assert "cauldron.c" in result
        assert "cauldron.d" in result


# ---------------------------------------------------------------------------
# Round-trip integration
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_save_then_load_then_apply(self, tmp_path):
        """Full round-trip: save → load → apply produces the expected state."""
        modules = {"cauldron.content": {}, "cauldron.auth": {}, "cauldron.admin": {}}
        overrides = {
            "cauldron.content": {"enabled": False},
            "cauldron.extra": {"enabled": True},
        }
        save_overlay(tmp_path, overrides)
        loaded, warning = load_overlay(tmp_path)
        assert warning is None
        result = apply_overlay(modules, loaded)
        assert "cauldron.content" not in result
        assert "cauldron.auth" in result
        assert "cauldron.admin" in result
        assert "cauldron.extra" in result
