"""Tests for the safe_resolve path helper."""
import os

import pytest

from cauldron_workspace_flatfile.paths import PathEscapeError, safe_resolve


def test_safe_resolve_simple(tmp_path):
    resolved = safe_resolve(tmp_path, "sub", "file.txt")
    assert str(resolved).startswith(str(tmp_path.resolve()))


def test_absolute_path_blocked(tmp_path):
    with pytest.raises(PathEscapeError):
        safe_resolve(tmp_path, "/etc/passwd")


def test_dotdot_traversal_blocked(tmp_path):
    with pytest.raises(PathEscapeError):
        safe_resolve(tmp_path, "..", "..", "etc")


def test_nested_dotdot_traversal_blocked(tmp_path):
    with pytest.raises(PathEscapeError):
        safe_resolve(tmp_path, "sub", "..", "..", "escape")


def test_symlink_escape_blocked(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    os.symlink(str(outside), str(link))
    with pytest.raises(PathEscapeError):
        safe_resolve(tmp_path, "link")


def test_normal_subdir_allowed(tmp_path):
    (tmp_path / "a").mkdir()
    resolved = safe_resolve(tmp_path, "a")
    assert resolved.name == "a"
