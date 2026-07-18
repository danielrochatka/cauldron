from django.apps import apps
from django.core import checks
from django.test import Client

import cauldron


def test_package_imports_with_authoritative_version():
    assert cauldron.__version__ == "0.1.0"


def test_django_app_initializes():
    config = apps.get_app_config("cauldron")
    assert config.name == "cauldron"


def test_system_checks_include_cauldron_foundation_info():
    messages = checks.run_checks()
    assert any(message.id == "cauldron.I001" for message in messages)
    assert not [message for message in messages if message.id.startswith("cauldron.E")]


def test_health_endpoint_returns_package_status():
    response = Client().get("/cauldron/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "package": "cauldron", "version": "0.1.0"}
