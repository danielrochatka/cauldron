"""
Test settings for cauldron-app integration tests.

Sets a deterministic SECRET_KEY in the process environment before importing
the production settings module, which requires the key to be present.
All other settings are inherited from cauldron_site.settings unchanged.
"""
import os

os.environ.setdefault(
    "SECRET_KEY",
    "cauldron-integration-test-secret-key-do-not-use-in-prod",
)

from cauldron_site.settings import *  # noqa: F401,F403,E402
