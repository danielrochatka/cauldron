"""WSGI config for django-state-consumer example."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "django_state_consumer.settings"
)

application = get_wsgi_application()
