"""Tests for cauldron_django_state.config."""
import pytest
from cauldron_django_state.config import DjangoStateConfig, sqlite_database


class TestSqliteDatabase:
    def test_returns_dict_with_engine(self):
        cfg = sqlite_database("/tmp/test.db")
        assert cfg["ENGINE"] == "django.db.backends.sqlite3"

    def test_returns_dict_with_name(self):
        cfg = sqlite_database("/tmp/test.db")
        assert cfg["NAME"] == "/tmp/test.db"

    def test_accepts_path_object(self):
        from pathlib import Path
        cfg = sqlite_database(Path("/tmp/test.db"))
        assert cfg["NAME"] == "/tmp/test.db"

    def test_returns_new_dict_each_time(self):
        cfg1 = sqlite_database("/tmp/a.db")
        cfg2 = sqlite_database("/tmp/a.db")
        assert cfg1 is not cfg2

    def test_does_not_mutate_input(self):
        """sqlite_database() must not modify its input."""
        path = "/tmp/test.db"
        original = str(path)
        sqlite_database(path)
        assert path == original


class TestDjangoStateConfig:
    def test_default_alias(self):
        cfg = DjangoStateConfig()
        assert cfg.database_alias == "default"

    def test_custom_alias(self):
        cfg = DjangoStateConfig(database_alias="replica")
        assert cfg.database_alias == "replica"

    def test_empty_alias_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            DjangoStateConfig(database_alias="")

    def test_non_string_alias_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            DjangoStateConfig(database_alias=123)  # type: ignore[arg-type]

    def test_from_module_config_default(self):
        cfg = DjangoStateConfig.from_module_config({})
        assert cfg.database_alias == "default"

    def test_from_module_config_custom(self):
        cfg = DjangoStateConfig.from_module_config({"database_alias": "replica"})
        assert cfg.database_alias == "replica"

    def test_database_alias_is_a_property(self):
        """database_alias should be accessible as a property."""
        cfg = DjangoStateConfig(database_alias="myalias")
        assert cfg.database_alias == "myalias"
        # The public property must not be directly settable.
        with pytest.raises(AttributeError):
            cfg.database_alias = "changed"  # type: ignore[misc]
