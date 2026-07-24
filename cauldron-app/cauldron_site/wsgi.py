"""WSGI config for the Cauldron self-hosted instance."""
import os
import sys
from pathlib import Path

# Ensure the cauldron-app directory is on the path so cauldron_site is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cauldron_site.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
