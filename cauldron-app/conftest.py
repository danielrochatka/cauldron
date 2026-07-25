"""
Root conftest for cauldron-app integration tests.

This file is at the pytest rootdir (where pytest.ini lives) so it is loaded
during pytest_configure, before pytest-django attempts to access Django
settings in pytest_load_initial_conftests.  SECRET_KEY must be in the
environment before cauldron_site.settings is imported.
"""
import os

os.environ.setdefault(
    "SECRET_KEY",
    "cauldron-integration-test-secret-key-do-not-use-in-prod",
)
