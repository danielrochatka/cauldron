"""Tests for the cauldron_ui_init management command."""
import pytest
from pathlib import Path

from django.core.management import call_command
from io import StringIO


@pytest.fixture()
def override_root(tmp_path):
    return tmp_path / "cauldron-overrides"


def test_init_creates_structure(override_root, settings):
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    out = StringIO()
    call_command("cauldron_ui_init", stdout=out)
    assert override_root.is_dir()
    assert (override_root / "admin").is_dir()
    assert (override_root / "pages").is_dir()
    assert (override_root / "admin" / "00-variables.css").is_file()
    assert (override_root / "admin" / "10-layout.css").is_file()
    assert (override_root / "admin" / "20-components.css").is_file()
    assert (override_root / "admin" / "90-site.css").is_file()
    assert (override_root / "pages" / "00-variables.css").is_file()
    assert (override_root / "pages" / "90-site.css").is_file()


def test_init_idempotent(override_root, settings):
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    call_command("cauldron_ui_init", stdout=StringIO())
    # Write custom content to a file
    custom_file = override_root / "admin" / "90-site.css"
    custom_content = "/* my custom styles */\n"
    custom_file.write_text(custom_content, encoding="utf-8")
    # Run again without --force
    call_command("cauldron_ui_init", stdout=StringIO())
    # File should not be overwritten
    assert custom_file.read_text(encoding="utf-8") == custom_content


def test_init_force_replaces(override_root, settings):
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    call_command("cauldron_ui_init", stdout=StringIO())
    # Write custom content
    custom_file = override_root / "admin" / "90-site.css"
    custom_file.write_text("/* custom */\n", encoding="utf-8")
    # Run with --force
    call_command("cauldron_ui_init", "--force", stdout=StringIO())
    # File should be reset to default
    content = custom_file.read_text(encoding="utf-8")
    assert "/* Site-wide admin CSS customizations */" in content


def test_check_mode_valid(override_root, settings):
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    # Initialize first
    call_command("cauldron_ui_init", stdout=StringIO())
    # Now check
    out = StringIO()
    call_command("cauldron_ui_init", "--check", stdout=out)
    output = out.getvalue()
    assert "valid" in output.lower()


def test_check_mode_missing(override_root, settings):
    from django.core.management.base import CommandError
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    # Do NOT initialize — root doesn't exist. --check must fail loudly so CI
    # can gate on it.
    err = StringIO()
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("cauldron_ui_init", "--check", stdout=out, stderr=err)
    combined = out.getvalue() + err.getvalue()
    assert (
        "issues" in combined.lower()
        or "warn" in combined.lower()
        or "does not exist" in combined.lower()
    )


def test_check_nonzero_on_missing_dir(override_root, settings):
    """`--check` must raise CommandError (non-zero exit) when the root is absent."""
    from django.core.management.base import CommandError
    settings.CAULDRON_UI_OVERRIDES_DIR = str(override_root)
    with pytest.raises(CommandError):
        call_command("cauldron_ui_init", "--check", stdout=StringIO(), stderr=StringIO())


def test_init_uses_base_dir(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    # Remove CAULDRON_UI_OVERRIDES_DIR if set
    if hasattr(settings, "CAULDRON_UI_OVERRIDES_DIR"):
        del settings.CAULDRON_UI_OVERRIDES_DIR
    out = StringIO()
    call_command("cauldron_ui_init", stdout=out)
    expected_root = tmp_path / "cauldron-overrides"
    assert expected_root.is_dir()
