"""Integration test: project module loaded into Django startup via subprocess."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def project_module_root(tmp_path):
    """Create a temp project module root with one lifecycle-aware module."""
    root = tmp_path / "modules"
    root.mkdir()
    pkg = root / "lifecycle_mod"
    pkg.mkdir()

    (pkg / "__init__.py").write_text(textwrap.dedent("""\
        import os as _os
        from cauldron.modules import BaseModule, ModuleManifest
        from lifecycle_mod.apps import LifecycleConfig

        _manifest = ModuleManifest(
            slug="lifecycle.test",
            label="Lifecycle Test",
            version="1.0.0",
            django_apps=("lifecycle_mod",),
        )


        class LifecycleModule(BaseModule):
            def register(self, context):
                import pathlib
                marker = pathlib.Path(_os.environ["_CAULDRON_TEST_MARKER_DIR"]) / "register.marker"
                marker.touch()

            def on_ready(self):
                import pathlib
                marker = pathlib.Path(_os.environ["_CAULDRON_TEST_MARKER_DIR"]) / "on_ready.marker"
                marker.touch()


        module = LifecycleModule(_manifest)
    """))

    (pkg / "apps.py").write_text(textwrap.dedent("""\
        from django.apps import AppConfig


        class LifecycleConfig(AppConfig):
            name = "lifecycle_mod"
            label = "lifecycle_mod"
    """))

    return root


def _run_subprocess(script: str, *, modules_root: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "SECRET_KEY": "subprocess-test-secret-key",
        "_CAULDRON_TEST_MODULES_ROOT": str(modules_root),
        "_CAULDRON_TEST_MARKER_DIR": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


_STARTUP_SCRIPT = textwrap.dedent("""\
    import os, sys
    modules_root = os.environ["_CAULDRON_TEST_MODULES_ROOT"]
    marker_dir = os.environ["_CAULDRON_TEST_MARKER_DIR"]

    # --- Settings-level composition (before django.setup()) ---
    from pathlib import Path
    from cauldron.django.compose import compose_django_settings

    plan = compose_django_settings(
        installed_apps=["cauldron"],
        module_settings={"lifecycle.test": {}},
        project_module_root=modules_root,
    )
    assert "lifecycle_mod" in plan.installed_apps, (
        f"lifecycle_mod not in installed_apps: {plan.installed_apps}"
    )
    print("APPS_BEFORE_SETUP:", ",".join(plan.installed_apps))

    # --- Full Django setup ---
    import django
    from django.conf import settings
    settings.configure(
        SECRET_KEY="subprocess-test-secret-key",
        INSTALLED_APPS=list(plan.installed_apps),
        DATABASES={},
        CAULDRON_MODULES={"lifecycle.test": {}},
        CAULDRON_PROJECT_MODULE_ROOT=modules_root,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

    from cauldron.modules.registry import registry
    records = [r for r in registry._discovery_records if r.source_type == "project"]
    assert len(records) == 1, f"Expected 1 project record, got {records}"
    rec = records[0]
    assert rec.slug == "lifecycle.test", rec.slug
    assert rec.project_path == "lifecycle_mod", rec.project_path

    register_marker = Path(marker_dir) / "register.marker"
    on_ready_marker = Path(marker_dir) / "on_ready.marker"
    assert register_marker.exists(), "register() was not called"
    assert on_ready_marker.exists(), "on_ready() was not called"
    print("OK")
""")


class TestProjectModuleDjangoStartup:
    def test_full_django_startup_with_project_module(self, project_module_root, tmp_path):
        result = _run_subprocess(
            _STARTUP_SCRIPT,
            modules_root=project_module_root,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, (
            f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout

    def test_project_app_in_installed_apps_before_setup(self, project_module_root, tmp_path):
        check_script = textwrap.dedent("""\
            import os
            modules_root = os.environ["_CAULDRON_TEST_MODULES_ROOT"]
            from cauldron.django.compose import compose_django_settings
            plan = compose_django_settings(
                installed_apps=["cauldron"],
                module_settings={"lifecycle.test": {}},
                project_module_root=modules_root,
            )
            assert "lifecycle_mod" in plan.installed_apps
            print("OK")
        """)
        result = _run_subprocess(
            check_script,
            modules_root=project_module_root,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_lifecycle_hooks_not_invoked_by_discovery_alone(self, project_module_root, tmp_path):
        check_script = textwrap.dedent("""\
            import os
            from pathlib import Path
            modules_root = os.environ["_CAULDRON_TEST_MODULES_ROOT"]
            marker_dir = os.environ["_CAULDRON_TEST_MARKER_DIR"]
            from cauldron.modules.discovery import discover_modules
            discover_modules(project_module_root=modules_root)
            register_marker = Path(marker_dir) / "register.marker"
            on_ready_marker = Path(marker_dir) / "on_ready.marker"
            assert not register_marker.exists(), "register() must not be called by discovery"
            assert not on_ready_marker.exists(), "on_ready() must not be called by discovery"
            print("OK")
        """)
        result = _run_subprocess(
            check_script,
            modules_root=project_module_root,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout
