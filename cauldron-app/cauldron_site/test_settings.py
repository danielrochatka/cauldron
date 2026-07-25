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

# CompressedManifestStaticFilesStorage requires collectstatic to have been run
# before any {% static %} tag can render (it needs the manifest file).  Use the
# simple backend in tests so requests work without a pre-existing staticfiles
# directory.  The whitenoise-static test overrides this explicitly.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
