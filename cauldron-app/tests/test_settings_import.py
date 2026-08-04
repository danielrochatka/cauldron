"""Regression test: production settings must import without error."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_production_settings_import_succeeds():
    env = {**os.environ, "SECRET_KEY": "test-secret-key-regression-test"}
    result = subprocess.run(
        [sys.executable, "-c",
         "import django; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', "
         "'cauldron_site.settings'); from cauldron_site import settings"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Settings import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
