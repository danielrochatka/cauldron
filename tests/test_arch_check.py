"""Tests for the arch_check tool itself.

Verifies that the architecture checker correctly detects violations when
given intentionally bad code. Uses temp files so no permanent ignore list
is needed.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def _run_arch_check(repo_root: Path) -> subprocess.CompletedProcess:
    """Run arch_check.py as a subprocess pointing at the given repo root."""
    arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
    return subprocess.run(
        [sys.executable, str(arch_check), "--root", str(repo_root)],
        capture_output=True,
        text=True,
    )


def test_arch_check_clean_on_real_repo():
    """The real Cauldron repo should have no violations after fixes."""
    repo_root = Path(__file__).resolve().parent.parent
    result = _run_arch_check(repo_root)
    assert result.returncode == 0, (
        f"arch_check found violations in the real repo:\n{result.stdout}\n{result.stderr}"
    )


def test_arch001_detected_for_undeclared_import():
    """ARCH001 is raised when a package imports from an undeclared sibling."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a fake "cauldron-content" package that owns cauldron_content
        _make_fake_package(
            root,
            pkg_name="cauldron-content",
            slug="cauldron.content",
            namespace="cauldron_content",
            public_api=["cauldron_content.contracts"],
            src_files={"contracts.py": "# public contracts\n"},
        )

        # Create a "cauldron-foo" package that imports cauldron_content WITHOUT
        # declaring it as a dependency in pyproject.toml or manifest
        _make_fake_package(
            root,
            pkg_name="cauldron-foo",
            slug="cauldron.foo",
            namespace="cauldron_foo",
            public_api=["cauldron_foo.api"],
            pyproject_deps=[],  # intentionally undeclared!
            src_files={
                "api.py": "from cauldron_content.contracts import ContentItem\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        "Expected arch_check to exit with 1 (violations found), "
        f"but got 0. Output:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout, (
        f"Expected ARCH001 in output, got:\n{result.stdout}"
    )
    assert "cauldron_content.contracts" in result.stdout


def test_arch002_detected_for_private_import():
    """ARCH002 is raised when a package imports from a private module of a sibling."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-bar",
            slug="cauldron.bar",
            namespace="cauldron_bar",
            public_api=["cauldron_bar.contracts"],
            src_files={
                "_internals.py": "SECRET = 42\n",
                "contracts.py": "# public\n",
            },
        )

        _make_fake_package(
            root,
            pkg_name="cauldron-baz",
            slug="cauldron.baz",
            namespace="cauldron_baz",
            public_api=["cauldron_baz.api"],
            pyproject_deps=["cauldron-bar"],
            src_files={
                "api.py": "from cauldron_bar._internals import SECRET\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH002" in result.stdout
    assert "cauldron_bar._internals" in result.stdout


def _make_fake_package(
    root: Path,
    *,
    pkg_name: str,
    slug: str,
    namespace: str,
    public_api: list[str],
    capability_implementations: list[str] | None = None,
    provides: list[str] | None = None,
    requires: list[tuple[str, str]] | None = None,  # [(slug, kind), ...]
    pyproject_deps: list[str] | None = None,
    src_files: dict[str, str] | None = None,
    test_files: dict[str, str] | None = None,
) -> None:
    """Create a minimal fake Cauldron package in root/packages/<pkg_name>/."""
    pkg_dir = root / "packages" / pkg_name
    src_ns_dir = pkg_dir / "src" / namespace
    src_ns_dir.mkdir(parents=True, exist_ok=True)

    public_api_str = ", ".join(f'"{p}"' for p in public_api)
    cap_impl_str = ", ".join(f'"{p}"' for p in (capability_implementations or []))
    provides_str = ", ".join(f'"{p}"' for p in (provides or []))

    requires_lines = ""
    if requires:
        req_items = ", ".join(
            f'ModuleRequirement(slug="{s}", kind="{k}")' for s, k in requires
        )
        requires_lines = f"    requires=({req_items},),\n"

    module_py = src_ns_dir / "module.py"
    module_py.write_text(
        f'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
        f'_manifest = ModuleManifest(\n'
        f'    slug="{slug}",\n'
        f'    label="{pkg_name}",\n'
        f'    namespaces=("{namespace}",),\n'
        f'    public_api=({public_api_str},),\n'
        + (f'    capability_implementations=({cap_impl_str},),\n' if capability_implementations else "")
        + (f'    provides=({provides_str},),\n' if provides else "")
        + requires_lines
        + f')\n'
        f'module = BaseModule(_manifest)\n',
        encoding="utf-8",
    )

    (src_ns_dir / "__init__.py").write_text("", encoding="utf-8")

    for filename, content in (src_files or {}).items():
        (src_ns_dir / filename).write_text(content, encoding="utf-8")

    if test_files:
        tests_dir = pkg_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        for filename, content in test_files.items():
            (tests_dir / filename).write_text(content, encoding="utf-8")

    if pyproject_deps is None:
        pyproject_deps = []
    extra_deps = "".join(f'    "{d}>=0.1.0",\n' for d in pyproject_deps)
    pyproject_toml = pkg_dir / "pyproject.toml"
    pyproject_toml.write_text(
        f'[build-system]\n'
        f'requires = ["setuptools>=68"]\n'
        f'build-backend = "setuptools.build_meta"\n\n'
        f'[project]\n'
        f'name = "{pkg_name}"\n'
        f'version = "0.1.0"\n'
        f'requires-python = ">=3.11"\n'
        f'dependencies = [\n'
        f'    "cauldron>=0.1.0",\n'
        f'{extra_deps}'
        f']\n',
        encoding="utf-8",
    )


def test_arch002b_detected_for_private_name_import():
    """ARCH002 fires for 'from pkg import _private_name' even when the module path is clean."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-provider", slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.contracts"],
            src_files={"contracts.py": "def _reset_for_tests(): pass\n"},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],
            requires=[("cauldron.provider", "module")],
            src_files={
                "api.py": "from cauldron_provider.contracts import _reset_for_tests\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH002" in result.stdout
    assert "_reset_for_tests" in result.stdout


def test_arch003_detected_for_capability_implementation_import():
    """ARCH003 fires when a package imports a path listed in capability_implementations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-site", slug="cauldron.site",
            namespace="cauldron_site",
            public_api=["cauldron_site.contracts", "cauldron_site.impl"],
            capability_implementations=["cauldron_site.impl"],
            provides=["site.public"],
            src_files={
                "contracts.py": "class SiteProvider: pass\n",
                "impl.py": "class ConcreteSiteProvider: pass\n",
            },
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-site"],
            requires=[("site.public", "capability")],
            src_files={
                "api.py": "from cauldron_site.impl import ConcreteSiteProvider\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH003" in result.stdout
    assert "cauldron_site.impl" in result.stdout


def test_arch003_not_raised_for_contract_import():
    """ARCH003 does not fire for paths NOT listed in capability_implementations.

    A consumer with a kind='module' dep on cauldron-site may import from
    cauldron_site.contracts (public API, not a capability_implementation) without
    triggering ARCH003. ARCH003 is reserved for cauldron_site.impl paths.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-site", slug="cauldron.site",
            namespace="cauldron_site",
            public_api=["cauldron_site.contracts", "cauldron_site.impl"],
            capability_implementations=["cauldron_site.impl"],
            provides=["site.public"],
            src_files={
                "contracts.py": "class SiteProvider: pass\n",
                "impl.py": "class ConcreteSiteProvider: pass\n",
            },
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-site"],
            # kind='module' is required because the consumer does direct Python imports
            requires=[("cauldron.site", "module")],
            src_files={
                "api.py": "from cauldron_site.contracts import SiteProvider\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, f"Unexpected violations:\n{result.stdout}"
    assert "ARCH003" not in result.stdout


def test_arch004_pyproject_has_dep_missing_from_manifest():
    """ARCH004 fires when pyproject main deps include a package absent from the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-base", slug="cauldron.base",
            namespace="cauldron_base",
            public_api=["cauldron_base.api"],
            provides=["base.capability"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-base"],  # in pyproject main deps
            # deliberately no requires= in manifest
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH004" in result.stdout
    assert "cauldron-base" in result.stdout


def test_arch004_optional_dep_not_flagged():
    """ARCH004 direction B does not fire for optional-only pyproject deps."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-plugin", slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            provides=["plugin.cap"],
            src_files={"api.py": ""},
        )

        # cauldron-consumer lists cauldron-plugin only in optional-dependencies,
        # not in main dependencies — ARCH004 direction B should not fire.
        pkg_dir = root / "packages" / "cauldron-consumer"
        src_ns_dir = pkg_dir / "src" / "cauldron_consumer"
        src_ns_dir.mkdir(parents=True)
        (src_ns_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_ns_dir / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="cauldron.consumer", label="Consumer",\n'
            '    namespaces=("cauldron_consumer",),\n'
            '    public_api=("cauldron_consumer.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns_dir / "api.py").write_text("", encoding="utf-8")
        (pkg_dir / "pyproject.toml").write_text(
            '[project]\nname = "cauldron-consumer"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = ["cauldron>=0.1.0"]\n'
            '[project.optional-dependencies]\nplugin = ["cauldron-plugin>=0.1.0"]\n',
            encoding="utf-8",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, f"Unexpected violations:\n{result.stdout}"


def test_duplicate_namespace_is_config_error():
    """Two packages claiming the same namespace produces a config error (exit 1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-alpha", slug="cauldron.alpha",
            namespace="cauldron_shared",
            public_api=["cauldron_shared.api"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root, pkg_name="cauldron-beta", slug="cauldron.beta",
            namespace="cauldron_shared",  # same namespace!
            public_api=["cauldron_shared.api"],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "DUPLICATE NAMESPACE" in result.stderr or "DUPLICATE NAMESPACE" in result.stdout


def test_arch001_detected_in_test_file():
    """ARCH001 fires in test files when importing from an undeclared main-dep sibling."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-provider", slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.api"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],  # in main deps
            # No manifest requires — should trigger ARCH001 in test file too
            test_files={
                "test_something.py": "from cauldron_provider.api import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH001" in result.stdout
    assert "test_something.py" in result.stdout


def test_arch002_detected_in_test_file():
    """ARCH002 fires in test files for private name imports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-internal", slug="cauldron.internal",
            namespace="cauldron_internal",
            public_api=["cauldron_internal.pub"],
            src_files={
                "pub.py": "",
                "_private.py": "SECRET = 1\n",
            },
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-internal"],
            requires=[("cauldron.internal", "module")],
            src_files={"api.py": ""},
            test_files={
                "test_something.py": "from cauldron_internal._private import SECRET\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH002" in result.stdout
    assert "test_something.py" in result.stdout


def test_test_file_optional_dep_import_allowed_without_manifest():
    """Test-file imports from optional-only pyproject deps require no manifest entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-plugin", slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": ""},
        )

        # cauldron-consumer lists cauldron-plugin only in optional-dependencies.
        # The test file imports from it — allowed since it's a test-only optional dep.
        pkg_dir = root / "packages" / "cauldron-consumer"
        src_ns_dir = pkg_dir / "src" / "cauldron_consumer"
        src_ns_dir.mkdir(parents=True)
        (src_ns_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_ns_dir / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="cauldron.consumer", label="Consumer",\n'
            '    namespaces=("cauldron_consumer",),\n'
            '    public_api=("cauldron_consumer.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns_dir / "api.py").write_text("", encoding="utf-8")
        tests_dir = pkg_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_with_plugin.py").write_text(
            "from cauldron_plugin.api import Thing\n",
            encoding="utf-8",
        )
        (pkg_dir / "pyproject.toml").write_text(
            '[project]\nname = "cauldron-consumer"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = ["cauldron>=0.1.0"]\n'
            '[project.optional-dependencies]\ntest = ["cauldron-plugin>=0.1.0"]\n',
            encoding="utf-8",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, f"Unexpected violations:\n{result.stdout}"


def test_arch002c_prefix_matching_allows_submodule():
    """Public API prefix matching allows imports from submodules of declared paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-lib", slug="cauldron.lib",
            namespace="cauldron_lib",
            public_api=["cauldron_lib.contracts"],  # prefix covers submodules
            src_files={
                "contracts.py": "",
                # A submodule of contracts
            },
        )
        # Create the submodule
        sub = root / "packages" / "cauldron-lib" / "src" / "cauldron_lib" / "contracts"
        sub.mkdir(exist_ok=True)
        (sub / "__init__.py").write_text("", encoding="utf-8")
        (sub / "types.py").write_text("class MyType: pass\n", encoding="utf-8")

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-lib"],
            requires=[("cauldron.lib", "module")],
            src_files={
                "api.py": "from cauldron_lib.contracts.types import MyType\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, f"Unexpected ARCH002 for submodule of public_api prefix:\n{result.stdout}"


def test_arch002c_similarly_prefixed_path_rejected():
    """A path that shares a prefix but is not a submodule is still rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-lib", slug="cauldron.lib",
            namespace="cauldron_lib",
            public_api=["cauldron_lib.contracts"],
            src_files={
                "contracts.py": "",
                "contracts_extra.py": "HIDDEN = 1\n",
            },
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-lib"],
            requires=[("cauldron.lib", "module")],
            src_files={
                "api.py": "from cauldron_lib.contracts_extra import HIDDEN\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH002" in result.stdout
    assert "contracts_extra" in result.stdout


def test_arch003_capability_impl_subpath_blocked():
    """ARCH003 fires for imports from subpaths of a capability_implementation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-site", slug="cauldron.site",
            namespace="cauldron_site",
            public_api=["cauldron_site.contracts", "cauldron_site.impl"],
            capability_implementations=["cauldron_site.impl"],
            provides=["site.public"],
            src_files={
                "contracts.py": "class SiteProvider: pass\n",
                "impl.py": "",
            },
        )
        # Create a submodule of impl
        impl_sub = root / "packages" / "cauldron-site" / "src" / "cauldron_site" / "impl"
        impl_sub.mkdir(exist_ok=True)
        (impl_sub / "__init__.py").write_text("", encoding="utf-8")
        (impl_sub / "core.py").write_text("class ConcreteProvider: pass\n", encoding="utf-8")

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-site"],
            requires=[("cauldron.site", "module")],
            src_files={
                # Import from a SUBPATH of a capability_implementation
                "api.py": "from cauldron_site.impl.core import ConcreteProvider\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1
    assert "ARCH003" in result.stdout


def test_module_root_project_modules_discovered():
    """--module-root causes project-owned modules to be included in namespace checks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create a packaged module that owns cauldron_core
        _make_fake_package(
            root, pkg_name="cauldron-core", slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": ""},
        )

        # Create a project-owned module under a custom modules/ dir
        mod_dir = root / "modules" / "my-project-module"
        src_ns = mod_dir / "src" / "my_project_module"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.project.module", label="My Project Module",\n'
            '    namespaces=("my_project_module",),\n'
            '    public_api=("my_project_module.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text(
            "from cauldron_core.api import Thing\n",  # undeclared dep
            encoding="utf-8",
        )

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    # The project module imports from cauldron_core without declaring it
    assert result.returncode == 1
    assert "ARCH001" in result.stdout


def test_duplicate_namespace_project_and_packaged():
    """A project module claiming a namespace already owned by a packaged module is an error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-alpha", slug="cauldron.alpha",
            namespace="cauldron_shared",
            public_api=["cauldron_shared.api"],
            src_files={"api.py": ""},
        )

        # Project-owned module claiming the same namespace
        mod_dir = root / "modules" / "project-alpha"
        src_ns = mod_dir / "src" / "cauldron_shared"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="project.alpha", label="Project Alpha",\n'
            '    namespaces=("cauldron_shared",),\n'  # duplicate!
            '    public_api=("cauldron_shared.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text("", encoding="utf-8")

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 1
    assert "DUPLICATE NAMESPACE" in (result.stdout + result.stderr)
