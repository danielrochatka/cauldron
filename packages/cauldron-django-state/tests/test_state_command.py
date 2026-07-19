"""Tests for the cauldron_state_status management command."""
import json
import pytest
from io import StringIO
from django.core.management import call_command


@pytest.mark.django_db
def test_human_readable_output_contains_alias():
    out = StringIO()
    call_command("cauldron_state_status", stdout=out)
    output = out.getvalue()
    assert "default" in output


@pytest.mark.django_db
def test_human_readable_output_contains_engine():
    out = StringIO()
    call_command("cauldron_state_status", stdout=out)
    output = out.getvalue()
    assert "sqlite3" in output.lower() or "ENGINE" in output or "django.db" in output


@pytest.mark.django_db
def test_human_readable_output_is_deterministic():
    out1 = StringIO()
    out2 = StringIO()
    call_command("cauldron_state_status", stdout=out1)
    call_command("cauldron_state_status", stdout=out2)
    assert out1.getvalue() == out2.getvalue()


@pytest.mark.django_db
def test_json_output_is_valid_json():
    out = StringIO()
    call_command("cauldron_state_status", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert isinstance(data, dict)


@pytest.mark.django_db
def test_json_output_has_required_keys():
    out = StringIO()
    call_command("cauldron_state_status", "--json", stdout=out)
    data = json.loads(out.getvalue())
    required_keys = {"database_alias", "engine", "vendor", "available", "name", "migration_state"}
    assert required_keys.issubset(data.keys())


@pytest.mark.django_db
def test_json_available_true_when_db_works():
    out = StringIO()
    call_command("cauldron_state_status", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert data["available"] is True


@pytest.mark.django_db
def test_json_database_alias_matches_settings():
    out = StringIO()
    call_command("cauldron_state_status", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert data["database_alias"] == "default"
