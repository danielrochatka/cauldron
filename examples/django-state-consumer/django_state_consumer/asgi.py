"""ASGI config for django-state-consumer example."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "django_state_consumer.settings"
)

application = get_asgi_application()
