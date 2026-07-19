"""Tests for cauldron_django_state.checks."""
import pytest


@pytest.fixture
def run_checks():
    """Run the cauldron_django_state checks and return messages."""
    def _run():
        import django
        from django.test.utils import setup_test_environment
        from cauldron_django_state.checks import check_state_config
        return check_state_config(app_configs=None)

    return _run


def test_valid_config_emits_info(run_checks):
    """Valid default config (alias='default', in DATABASES) → I001 info."""
    results = run_checks()
    ids = [r.id for r in results]
    assert "cauldron.state.I001" in ids


def test_no_errors_for_valid_config(run_checks):
    """Valid config → no error-level messages."""
    from django.core.checks import Error
    results = run_checks()
    errors = [r for r in results if isinstance(r, Error)]
    assert not errors


def test_unknown_alias_emits_e101(settings):
    """Unknown database alias → cauldron.state.E101."""
    from cauldron_django_state.checks import check_state_config

    settings.CAULDRON_MODULES = {"cauldron.django.state": {"database_alias": "nonexistent"}}
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

    results = check_state_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.state.E101" in ids


def test_invalid_alias_type_emits_e100(settings):
    """Non-string alias → cauldron.state.E100."""
    from cauldron_django_state.checks import check_state_config

    settings.CAULDRON_MODULES = {"cauldron.django.state": {"database_alias": 123}}
    results = check_state_config(app_configs=None)
    ids = [r.id for r in results]
    assert "cauldron.state.E100" in ids


def test_module_not_active_returns_empty(settings):
    """When cauldron.django.state is not in CAULDRON_MODULES, checks return empty."""
    from cauldron_django_state.checks import check_state_config

    settings.CAULDRON_MODULES = {}
    results = check_state_config(app_configs=None)
    assert results == []
