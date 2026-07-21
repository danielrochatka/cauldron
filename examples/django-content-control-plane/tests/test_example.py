"""Integration tests for the content control plane example."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _run(*args):
    result = subprocess.run(
        [PYTHON, str(EXAMPLE_DIR / "manage.py")] + list(args),
        capture_output=True,
        text=True,
        cwd=str(EXAMPLE_DIR),
    )
    return result


def test_django_check_passes():
    result = _run("check")
    assert result.returncode == 0, f"manage.py check failed:\n{result.stderr}"


@pytest.mark.django_db
def test_migrations_applied():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert plan == [], f"Pending migrations: {plan}"


def test_content_operations_status():
    result = _run("cauldron_content_operations_status")
    assert result.returncode == 0
    assert "content" in result.stdout.lower()


def test_content_operations_status_json():
    result = _run("cauldron_content_operations_status", "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["module"] == "cauldron.content.operations"


def test_reconcile_dry_run():
    result = _run("cauldron_content_reconcile", "--dry-run")
    assert result.returncode == 0
    assert "reconciliation" in result.stdout.lower() or "total" in result.stdout.lower()


def test_reconcile_dry_run_json():
    result = _run("cauldron_content_reconcile", "--dry-run", "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["dry_run"] is True


def test_api_urls_resolve():
    from django.urls import reverse
    url = reverse("cauldron_content_api:collections-list")
    assert url == "/cauldron/api/v1/collections/"


@pytest.mark.django_db
def test_change_request_model():
    from cauldron_content_operations.models import ContentChangeRequest
    count = ContentChangeRequest.objects.count()
    assert isinstance(count, int)


@pytest.mark.django_db
def test_permissions_exist():
    from django.contrib.auth.models import Permission
    perm = Permission.objects.filter(codename="view_published_content").first()
    assert perm is not None
