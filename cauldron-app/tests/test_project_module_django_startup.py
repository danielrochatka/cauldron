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
                log = pathlib.Path(_os.environ["_CAULDRON_TEST_MARKER_DIR"]) / "events.log"
                with open(log, "a") as f:
                    f.write("register\\n")

            def on_ready(self):
                import pathlib
                log = pathlib.Path(_os.environ["_CAULDRON_TEST_MARKER_DIR"]) / "events.log"
                with open(log, "a") as f:
                    f.write("on_ready\\n")


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

    from pathlib import Path
    from cauldron.django.compose import compose_django_settings

    # Snapshot sys.path before composition
    sys_path_before = list(sys.path)

    plan = compose_django_settings(
        installed_apps=["cauldron"],
        module_settings={"lifecycle.test": {}},
        project_module_root=modules_root,
    )
    assert "lifecycle_mod" in plan.installed_apps, (
        f"lifecycle_mod not in installed_apps: {plan.installed_apps}"
    )

    # sys.path must be identical after composition
    assert sys.path == sys_path_before, (
        f"sys.path changed after compose_django_settings: before={sys_path_before}, after={sys.path}"
    )
    print("APPS_BEFORE_SETUP:", ",".join(plan.installed_apps))

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

    # sys.path must still be identical after django.setup()
    assert sys.path == sys_path_before, (
        f"sys.path changed after django.setup(): before={sys_path_before}, after={sys.path}"
    )

    from cauldron.modules.registry import registry
    records = [r for r in registry._discovery_records if r.source_type == "project"]
    assert len(records) == 1, f"Expected 1 project record, got {records}"
    rec = records[0]
    assert rec.slug == "lifecycle.test", rec.slug
    assert rec.project_path == "lifecycle_mod", rec.project_path

    log_path = Path(marker_dir) / "events.log"
    assert log_path.exists(), "No lifecycle events recorded"
    lines = log_path.read_text().strip().splitlines()
    assert lines.count("register") == 1, f"Expected register=1, got: {lines}"
    assert lines.count("on_ready") == 1, f"Expected on_ready=1, got: {lines}"
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
            log_path = Path(marker_dir) / "events.log"
            assert not log_path.exists() or log_path.read_text().strip() == "", (
                "register() or on_ready() must not be called by discovery alone"
            )
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

    def test_cross_source_dependency_order(self, tmp_path):
        """project module requiring a packaged dep must appear after it in module_order."""
        # Create a standalone project root for this test
        modules_root = tmp_path / "cross_modules"
        modules_root.mkdir()

        # dep_user project module that requires("packaged.dep")
        dep_user_pkg = modules_root / "dep_user"
        dep_user_pkg.mkdir()
        (dep_user_pkg / "__init__.py").write_text(textwrap.dedent("""\
            from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement
            module = BaseModule(ModuleManifest(
                slug="dep.user",
                label="Dep User",
                version="1.0.0",
                requires=(ModuleRequirement(slug="packaged.dep"),),
            ))
        """))

        cross_script = textwrap.dedent("""\
            import os, sys, types, textwrap, tempfile
            from pathlib import Path
            from unittest.mock import patch

            modules_root = os.environ["_CAULDRON_TEST_MODULES_ROOT"]

            # Create a minimal in-process "packaged" module as an entry point
            from cauldron.modules import BaseModule, ModuleManifest
            packaged_dep_obj = BaseModule(ModuleManifest(
                slug="packaged.dep",
                label="Packaged Dep",
                version="1.0.0",
            ))

            class FakeEP:
                name = "packaged.dep"
                value = "packaged_dep_inline:module"
                dist = None
                def load(self):
                    return packaged_dep_obj

            from cauldron.django.compose import compose_django_settings

            with patch("cauldron.modules.discovery.entry_points", return_value=[FakeEP()]):
                plan = compose_django_settings(
                    installed_apps=["cauldron"],
                    module_settings={"dep.user": {}, "packaged.dep": {}},
                    project_module_root=modules_root,
                )

            order = list(plan.module_order)
            assert "packaged.dep" in order, f"packaged.dep missing from order: {order}"
            assert "dep.user" in order, f"dep.user missing from order: {order}"
            packaged_idx = order.index("packaged.dep")
            dep_user_idx = order.index("dep.user")
            assert packaged_idx < dep_user_idx, (
                f"packaged.dep must come before dep.user in module_order; got: {order}"
            )
            print("OK")
        """)
        result = _run_subprocess(
            cross_script,
            modules_root=modules_root,
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, (
            f"Cross-source dep-order failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout
