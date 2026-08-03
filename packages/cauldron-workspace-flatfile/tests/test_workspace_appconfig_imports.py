"""Regression tests for required AppConfig imports.

The workspace AppConfig depends on the canonical reversible contract and its
local configuration/adapter modules. Import failures must abort startup rather
than being converted into silent registration no-ops.
"""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


@pytest.mark.parametrize(
    ("blocked_name", "blocked_level"),
    [
        ("cauldron_content.reversible", 0),
        ("config", 1),
        ("reversible", 1),
    ],
)
def test_required_appconfig_import_error_propagates(
    monkeypatch,
    blocked_name: str,
    blocked_level: int,
) -> None:
    """Canonical and local import failures remain visible during startup."""
    module_name = "cauldron_workspace_flatfile.apps"
    original_module = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def failing_import(
        name,
        globals=None,
        locals=None,
        fromlist=(),
        level=0,
    ):
        if name == blocked_name and level == blocked_level:
            raise ImportError(f"required import unavailable: {blocked_name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    try:
        with pytest.raises(ImportError, match="required import unavailable"):
            importlib.import_module(module_name)
    finally:
        sys.modules.pop(module_name, None)
        if original_module is not None:
            sys.modules[module_name] = original_module
