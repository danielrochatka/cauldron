"""Tests for CauldronWorkspaceFlatfileConfig._maybe_register_reversible_adapter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cauldron_content.reversible import reset_registry
from cauldron_workspace_flatfile.apps import CauldronWorkspaceFlatfileConfig


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the canonical reversible registry around every test."""
    reset_registry()
    yield
    reset_registry()


def _make_app() -> CauldronWorkspaceFlatfileConfig:
    return CauldronWorkspaceFlatfileConfig("cauldron_workspace_flatfile", None)


def _modules_setting(*, workspace_root=None, content_root=None) -> dict:
    cfg: dict = {}
    if workspace_root is not None:
        cfg["cauldron.workspace.flatfile"] = {"workspace_root": workspace_root}
    if content_root is not None:
        cfg["cauldron.cms.flatfile"] = {"content_root": content_root}
    return cfg


class TestMaybeRegisterReversibleAdapter:
    def test_missing_workspace_root_is_noop(self, tmp_path):
        """No workspace_root → clean early return, no adapter registered."""
        setting = _modules_setting(content_root=str(tmp_path / "content"))
        with patch("django.conf.settings", CAULDRON_MODULES=setting):
            _make_app()._maybe_register_reversible_adapter()
        from cauldron_content.reversible import get_adapter
        assert get_adapter("flatfile") is None

    def test_missing_content_root_is_noop(self, tmp_path):
        """No content_root → clean early return, no adapter registered."""
        setting = _modules_setting(workspace_root=str(tmp_path / "ws"))
        with patch("django.conf.settings", CAULDRON_MODULES=setting):
            _make_app()._maybe_register_reversible_adapter()
        from cauldron_content.reversible import get_adapter
        assert get_adapter("flatfile") is None

    def test_both_roots_missing_is_noop(self):
        """Empty CAULDRON_MODULES → clean early return."""
        with patch("django.conf.settings", CAULDRON_MODULES={}):
            _make_app()._maybe_register_reversible_adapter()
        from cauldron_content.reversible import get_adapter
        assert get_adapter("flatfile") is None

    def test_valid_config_registers_adapter(self, tmp_path):
        """Valid workspace_root and content_root → FlatFileReversibleMutationAdapter registered."""
        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        with patch("django.conf.settings", CAULDRON_MODULES=setting):
            _make_app()._maybe_register_reversible_adapter()

        from cauldron_content.reversible import get_adapter
        from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter
        adapter = get_adapter("flatfile")
        assert isinstance(adapter, FlatFileReversibleMutationAdapter)

    def test_existing_adapter_is_preserved(self, tmp_path):
        """Pre-existing 'flatfile' registration is left untouched (idempotent)."""
        sentinel = MagicMock()
        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        # Inject the sentinel bypassing contract validation.
        with patch.dict("cauldron_content.reversible._registry", {"flatfile": sentinel}):
            with patch("django.conf.settings", CAULDRON_MODULES=setting):
                _make_app()._maybe_register_reversible_adapter()
            from cauldron_content.reversible import get_adapter
            assert get_adapter("flatfile") is sentinel

    def test_idempotent_repeated_registration(self, tmp_path):
        """Calling twice does not overwrite the first registration."""
        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        app = _make_app()
        with patch("django.conf.settings", CAULDRON_MODULES=setting):
            app._maybe_register_reversible_adapter()
            app._maybe_register_reversible_adapter()

        from cauldron_content.reversible import get_adapter
        from cauldron_workspace_flatfile.reversible import FlatFileReversibleMutationAdapter
        assert isinstance(get_adapter("flatfile"), FlatFileReversibleMutationAdapter)

    def test_workspace_config_validation_failure_propagates(self, tmp_path):
        """WorkspaceConfig raising at construction must not be silenced."""
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root="/valid-looking/path",
            content_root=str(content),
        )
        with patch(
            "cauldron_workspace_flatfile.apps.WorkspaceConfig",
            side_effect=ValueError("invalid workspace layout"),
        ):
            with patch("django.conf.settings", CAULDRON_MODULES=setting):
                with pytest.raises(ValueError, match="invalid workspace layout"):
                    _make_app()._maybe_register_reversible_adapter()

    def test_adapter_construction_failure_propagates(self, tmp_path):
        """FlatFileReversibleMutationAdapter raising must not be silenced."""
        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        with patch(
            "cauldron_workspace_flatfile.apps.FlatFileReversibleMutationAdapter",
            side_effect=RuntimeError("disk layout invalid"),
        ):
            with patch("django.conf.settings", CAULDRON_MODULES=setting):
                with pytest.raises(RuntimeError, match="disk layout invalid"):
                    _make_app()._maybe_register_reversible_adapter()

    def test_adapter_version_mismatch_propagates(self, tmp_path):
        """AdapterVersionMismatch from register_adapter must not be silenced."""
        from cauldron_content.reversible import AdapterVersionMismatch

        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        with patch(
            "cauldron_workspace_flatfile.apps.register_adapter",
            side_effect=AdapterVersionMismatch("version mismatch"),
        ):
            with patch("django.conf.settings", CAULDRON_MODULES=setting):
                with pytest.raises(AdapterVersionMismatch):
                    _make_app()._maybe_register_reversible_adapter()

    def test_get_adapter_error_propagates(self, tmp_path):
        """Errors from get_adapter (e.g. a broken install) are not swallowed.

        The required imports (cauldron_content.reversible, .config, .reversible)
        now happen at module level, so an ImportError there fails the entire
        apps.py import rather than silently returning None.  This test proves
        the equivalent runtime behaviour: an unexpected error from any required
        call site must propagate rather than being converted into a no-op.
        """
        ws = tmp_path / "ws"
        content = tmp_path / "content"
        content.mkdir()
        setting = _modules_setting(
            workspace_root=str(ws),
            content_root=str(content),
        )
        with patch(
            "cauldron_workspace_flatfile.apps.get_adapter",
            side_effect=RuntimeError("registry corrupted"),
        ):
            with patch("django.conf.settings", CAULDRON_MODULES=setting):
                with pytest.raises(RuntimeError, match="registry corrupted"):
                    _make_app()._maybe_register_reversible_adapter()
