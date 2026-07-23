"""Tests for ContentOperationsConfig."""
import pytest
from cauldron_content_operations.config import ContentOperationsConfig


def test_default_config():
    cfg = ContentOperationsConfig()
    assert cfg.require_approval is True
    assert cfg.allow_self_approval is False
    assert cfg.max_operations_per_change_set == 100


def test_invalid_max_ops():
    with pytest.raises(TypeError):
        ContentOperationsConfig(max_operations_per_change_set=0)


def test_invalid_require_approval():
    with pytest.raises(TypeError):
        ContentOperationsConfig(require_approval="yes")


def test_config_is_frozen():
    cfg = ContentOperationsConfig()
    with pytest.raises(Exception):
        cfg.require_approval = False
