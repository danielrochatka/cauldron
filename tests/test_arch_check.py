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
    optional_requires: list[tuple[str, str]] | None = None,  # for manifest optional=
    pyproject_deps: list[str] | None = None,
    pyproject_optional_groups: dict[str, list[str]] | None = None,  # for optional dep groups
    src_files: dict[str, str] | None = None,
    test_files: dict[str, str] | None = None,
) -> None:
    """Create a minimal fake Cauldron package in root/packages/<pkg_name>/."""
    pkg_dir = root / "packages" / pkg_name
    ns_parts = namespace.split(".")
    src_ns_dir = pkg_dir / "src" / Path(*ns_parts)
    # Create each ancestor directory with __init__.py
    parent = pkg_dir / "src"
    parent.mkdir(parents=True, exist_ok=True)
    for part in ns_parts:
        parent = parent / part
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "__init__.py").write_text("", encoding="utf-8")

    public_api_str = ", ".join(f'"{p}"' for p in public_api)
    cap_impl_str = ", ".join(f'"{p}"' for p in (capability_implementations or []))
    provides_str = ", ".join(f'"{p}"' for p in (provides or []))

    requires_lines = ""
    if requires:
        req_items = ", ".join(
            f'ModuleRequirement(slug="{s}", kind="{k}")' for s, k in requires
        )
        requires_lines = f"    requires=({req_items},),\n"

    optional_requires_lines = ""
    if optional_requires:
        opt_items = ", ".join(
            f'ModuleRequirement(slug="{s}", kind="{k}")' for s, k in optional_requires
        )
        optional_requires_lines = f"    optional=({opt_items},),\n"

    def _tuple_literal(items_str: str) -> str:
        """Render a tuple literal from a comma-separated items string."""
        if not items_str:
            return "()"
        return f"({items_str},)"

    module_py = src_ns_dir / "module.py"
    module_py.write_text(
        f'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
        f'_manifest = ModuleManifest(\n'
        f'    slug="{slug}",\n'
        f'    label="{pkg_name}",\n'
        f'    namespaces=("{namespace}",),\n'
        f'    public_api={_tuple_literal(public_api_str)},\n'
        + (f'    capability_implementations={_tuple_literal(cap_impl_str)},\n' if capability_implementations else "")
        + (f'    provides={_tuple_literal(provides_str)},\n' if provides else "")
        + requires_lines
        + optional_requires_lines
        + f')\n'
        f'module = BaseModule(_manifest)\n',
        encoding="utf-8",
    )

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

    optional_groups_toml = ""
    if pyproject_optional_groups:
        optional_groups_toml = "\n[project.optional-dependencies]\n"
        for group_name, group_deps in pyproject_optional_groups.items():
            deps_list = ", ".join(f'"{d}>=0.1.0"' for d in group_deps)
            optional_groups_toml += f'{group_name} = [{deps_list}]\n'

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
        f']\n'
        + optional_groups_toml,
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


def test_arch004_optional_dep_missing_manifest_fires():
    """ARCH004 fires when a runtime-optional pyproject dep has no manifest optional= entry."""
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
        # not in main dependencies, and no manifest optional= entry — ARCH004 should fire.
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

    assert result.returncode == 1, f"Expected ARCH004 for missing manifest optional= entry:\n{result.stdout}"
    assert "ARCH004" in result.stdout


def test_arch004_optional_dep_with_module_relation_clean():
    """Runtime-optional dep with a matching optional= module entry passes cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-plugin", slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            provides=["plugin.cap"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],
            pyproject_optional_groups={"plugin": ["cauldron-plugin"]},
            optional_requires=[("cauldron.plugin", "module")],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Runtime-optional dep with optional= module entry should pass:\n{result.stdout}"
    )


def test_arch004_optional_dep_with_capability_relation_clean():
    """Runtime-optional dep with a matching optional= capability entry passes cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-plugin", slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            provides=["plugin.cap"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],
            pyproject_optional_groups={"plugin": ["cauldron-plugin"]},
            optional_requires=[("plugin.cap", "capability")],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Runtime-optional dep with optional= capability entry should pass:\n{result.stdout}"
    )


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


# ---------------------------------------------------------------------------
# New tests for dotted namespaces, overlapping namespaces, and new ARCH004 checks
# ---------------------------------------------------------------------------

def test_dotted_namespace_self_import_allowed():
    """A package owning a genuine dotted namespace can import within itself freely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package owns myapp.core dotted namespace
        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api", "myapp.core.utils"],
            src_files={
                "api.py": "from myapp.core.utils import helper\n",
                "utils.py": "def helper(): pass\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, f"Self-import within same namespace should be allowed:\n{result.stdout}"


def test_dotted_namespace_cross_module_import():
    """Package A importing from Package B's dotted namespace without a dep declaration raises ARCH001."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package B owns myapp.core dotted namespace
        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.utils"],
            src_files={"utils.py": "class Thing: pass\n"},
        )

        # Package A owns myapp.api and imports from myapp.core without any dep
        _make_fake_package(
            root,
            pkg_name="myapp-api",
            slug="myapp.api",
            namespace="myapp.api",
            public_api=["myapp.api.views"],
            pyproject_deps=[],  # no dep on myapp-core!
            src_files={
                "views.py": "from myapp.core.utils import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, f"Expected ARCH001 for undeclared cross-module import:\n{result.stdout}"
    assert "ARCH001" in result.stdout


def test_parent_child_namespace_conflict():
    """Package A claiming 'myapp' and Package B claiming 'myapp.core' produces a config error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package A claims myapp namespace
        pkg_a_dir = root / "packages" / "cauldron-myapp"
        src_a_dir = pkg_a_dir / "src" / "myapp"
        src_a_dir.mkdir(parents=True, exist_ok=True)
        (src_a_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_a_dir / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="cauldron.myapp",\n'
            '    label="MyApp",\n'
            '    namespaces=("myapp",),\n'
            '    public_api=("myapp.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (pkg_a_dir / "pyproject.toml").write_text(
            '[project]\nname = "cauldron-myapp"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = ["cauldron>=0.1.0"]\n',
            encoding="utf-8",
        )

        # Package B claims myapp.core namespace (child of myapp)
        pkg_b_dir = root / "packages" / "cauldron-myapp-core"
        src_b_dir = pkg_b_dir / "src" / "myapp_core"
        src_b_dir.mkdir(parents=True, exist_ok=True)
        (src_b_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_b_dir / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="cauldron.myapp.core",\n'
            '    label="MyApp Core",\n'
            '    namespaces=("myapp", "myapp.core"),\n'
            '    public_api=("myapp.core.api",),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (pkg_b_dir / "pyproject.toml").write_text(
            '[project]\nname = "cauldron-myapp-core"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = ["cauldron>=0.1.0"]\n',
            encoding="utf-8",
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, "Expected config error for overlapping namespaces"
    combined = result.stdout + result.stderr
    assert "OVERLAPPING NAMESPACE" in combined or "DUPLICATE NAMESPACE" in combined, (
        f"Expected namespace conflict error, got:\n{combined}"
    )


def test_sibling_dotted_namespaces_allowed():
    """Package A claiming 'myapp.core' and Package B claiming 'myapp.extensions' is fine."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package A claims myapp_core namespace
        _make_fake_package(
            root,
            pkg_name="cauldron-myapp-core",
            slug="cauldron.myapp.core",
            namespace="myapp_core",
            public_api=["myapp_core.api"],
            src_files={"api.py": ""},
        )

        # Package B claims myapp_extensions namespace (sibling, not parent/child)
        _make_fake_package(
            root,
            pkg_name="cauldron-myapp-extensions",
            slug="cauldron.myapp.extensions",
            namespace="myapp_extensions",
            public_api=["myapp_extensions.api"],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Sibling dotted namespaces should not produce a config error:\n{result.stdout}\n{result.stderr}"
    )
    assert "OVERLAPPING NAMESPACE" not in (result.stdout + result.stderr)


def test_similarly_prefixed_namespace_not_parent_child():
    """Package A claiming 'myapp' and Package B claiming 'myapp_extra' is NOT a conflict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Package A claims myapp namespace
        _make_fake_package(
            root,
            pkg_name="cauldron-myapp",
            slug="cauldron.myapp",
            namespace="myapp",
            public_api=["myapp.api"],
            src_files={"api.py": ""},
        )

        # Package B claims myapp_extra namespace (different root, not a child)
        _make_fake_package(
            root,
            pkg_name="cauldron-myapp-extra",
            slug="cauldron.myapp.extra",
            namespace="myapp_extra",
            public_api=["myapp_extra.api"],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Similarly-prefixed but separate namespaces should not conflict:\n{result.stdout}\n{result.stderr}"
    )
    assert "OVERLAPPING NAMESPACE" not in (result.stdout + result.stderr)


def test_arch004_main_dep_in_optional_manifest_raises():
    """ARCH004 fires when a main dep is declared as optional= in the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-base",
            slug="cauldron.base",
            namespace="cauldron_base",
            public_api=["cauldron_base.api"],
            src_files={"api.py": ""},
        )

        # cauldron-consumer has cauldron-base in MAIN deps but manifest puts it in optional=
        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-base"],  # main dep
            optional_requires=[("cauldron.base", "module")],  # but manifest says optional=!
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH004 for main dep in optional= manifest:\n{result.stdout}"
    )
    assert "ARCH004" in result.stdout, f"Expected ARCH004 in output:\n{result.stdout}"


def test_arch004_runtime_optional_dep_in_requires_manifest_raises():
    """ARCH004 fires when a runtime-optional dep is declared under requires= in the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-base",
            slug="cauldron.base",
            namespace="cauldron_base",
            public_api=["cauldron_base.api"],
            src_files={"api.py": ""},
        )

        # cauldron-consumer has cauldron-base in runtime-optional group (not main deps)
        # but manifest puts it in requires= (which is for main deps)
        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],  # NOT in main deps
            pyproject_optional_groups={"extras": ["cauldron-base"]},  # only in non-test optional
            requires=[("cauldron.base", "module")],  # but manifest says requires=!
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH004 for runtime-optional dep in requires= manifest:\n{result.stdout}"
    )
    assert "ARCH004" in result.stdout, f"Expected ARCH004 in output:\n{result.stdout}"


def test_project_module_arch004_violation():
    """Project module with pyproject.toml and undeclared manifest dep raises ARCH004."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # A packaged module that is referenced
        _make_fake_package(
            root,
            pkg_name="cauldron-core",
            slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": ""},
        )

        # A project module with pyproject.toml that has main dep on cauldron-core
        # but no manifest declaration
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
            # No requires= declared!
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text("", encoding="utf-8")
        # Project module has pyproject.toml with main dep on cauldron-core
        (mod_dir / "pyproject.toml").write_text(
            '[project]\nname = "my-project-module"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            'dependencies = ["cauldron>=0.1.0", "cauldron-core>=0.1.0"]\n',
            encoding="utf-8",
        )

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 1, (
        f"Expected ARCH004 for project module with undeclared manifest dep:\n{result.stdout}"
    )
    assert "ARCH004" in result.stdout, f"Expected ARCH004 in output:\n{result.stdout}"


def test_project_module_arch004_clean():
    """Project module with correct pyproject + manifest declarations passes cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # A packaged module that is referenced
        _make_fake_package(
            root,
            pkg_name="cauldron-core",
            slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": ""},
        )

        # A project module with pyproject.toml that correctly declares cauldron-core
        mod_dir = root / "modules" / "my-project-module"
        src_ns = mod_dir / "src" / "my_project_module"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.project.module", label="My Project Module",\n'
            '    namespaces=("my_project_module",),\n'
            '    public_api=("my_project_module.api",),\n'
            '    requires=(ModuleRequirement(slug="cauldron.core", kind="module"),),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text("", encoding="utf-8")
        (mod_dir / "pyproject.toml").write_text(
            '[project]\nname = "my-project-module"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            'dependencies = ["cauldron>=0.1.0", "cauldron-core>=0.1.0"]\n',
            encoding="utf-8",
        )

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 0, (
        f"Expected clean result for project module with correct declarations:\n{result.stdout}"
    )


def test_unpackaged_project_module_no_arch004():
    """Project module with no pyproject.toml does not fire ARCH004."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # A packaged module that is referenced
        _make_fake_package(
            root,
            pkg_name="cauldron-core",
            slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": ""},
        )

        # A project module WITHOUT pyproject.toml, but with a manifest dep on cauldron-core
        mod_dir = root / "modules" / "my-project-module"
        src_ns = mod_dir / "src" / "my_project_module"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.project.module", label="My Project Module",\n'
            '    namespaces=("my_project_module",),\n'
            '    public_api=("my_project_module.api",),\n'
            '    requires=(ModuleRequirement(slug="cauldron.core", kind="module"),),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text("", encoding="utf-8")
        # No pyproject.toml here!

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 0, (
        f"Project module without pyproject.toml should NOT fire ARCH004:\n{result.stdout}"
    )


def test_overlapping_module_roots_stable():
    """Two --module-root flags pointing to directories sharing the same module namespace produce a stable config error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create two module roots that both have a module claiming the same namespace
        for root_name in ("modules_a", "modules_b"):
            mod_dir = root / root_name / "shared-module"
            src_ns = mod_dir / "src" / "cauldron_shared"
            src_ns.mkdir(parents=True)
            (src_ns / "__init__.py").write_text("", encoding="utf-8")
            (src_ns / "module.py").write_text(
                'from cauldron.modules import BaseModule, ModuleManifest\n'
                '_manifest = ModuleManifest(\n'
                '    slug="cauldron.shared",\n'
                '    label="Shared Module",\n'
                '    namespaces=("cauldron_shared",),\n'
                '    public_api=("cauldron_shared.api",),\n'
                ')\n'
                'module = BaseModule(_manifest)\n',
                encoding="utf-8",
            )
            (src_ns / "api.py").write_text("", encoding="utf-8")

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules_a"),
             "--module-root", str(root / "modules_b")],
            capture_output=True, text=True,
        )

    # Should produce a config error (DUPLICATE NAMESPACE), not crash
    assert result.returncode == 1, (
        f"Expected config error for overlapping module roots:\n{result.stdout}\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "DUPLICATE NAMESPACE" in combined, (
        f"Expected DUPLICATE NAMESPACE error:\n{combined}"
    )


def test_root_checker_clean_without_fixture_pyproject_entries():
    """Integration root test files can import from any discovered package (no ARCH001)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Root has no cauldron deps, a discovered package exists
        _make_fake_package(
            root,
            pkg_name="cauldron-util",
            slug="cauldron.util",
            namespace="cauldron_util",
            public_api=["cauldron_util.api"],
            src_files={"api.py": ""},
        )

        # Create root/src/cauldron/__init__.py (the framework, no module.py)
        root_src = root / "src" / "cauldron"
        root_src.mkdir(parents=True)
        (root_src / "__init__.py").write_text("", encoding="utf-8")

        # Root test file imports from cauldron_util without any pyproject declaration
        root_tests = root / "tests"
        root_tests.mkdir()
        (root_tests / "test_something.py").write_text(
            "from cauldron_util.api import Thing\n",  # no pyproject decl
            encoding="utf-8",
        )

        # Root pyproject has no cauldron-* deps
        (root / "pyproject.toml").write_text(
            '[project]\nname="cauldron"\nversion="0.1.0"\n'
            'dependencies=["Django>=5.0"]\n',  # no cauldron-* deps
            encoding="utf-8",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Integration root test should be allowed:\n{result.stdout}"
    )


def test_root_checker_clean_undiscovered_fixture_import():
    """Integration root tests may import from fixture packages not in packages/ (no ARCH001).

    Fixtures like cauldron_fixture_alpha live in tests/fixtures/, not packages/.
    They are not discovered by the default scan and have no pyproject entry in the
    root. The checker must still pass — the repo checkout is the declaration.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # No packages/ at all — simulates importing from a fixture not in packages/
        root_src = root / "src" / "cauldron"
        root_src.mkdir(parents=True)
        (root_src / "__init__.py").write_text("", encoding="utf-8")

        root_tests = root / "tests"
        root_tests.mkdir()
        # Import from cauldron_fixture_alpha — NOT a discovered package
        (root_tests / "test_discovery.py").write_text(
            "from cauldron_fixture_alpha import module as alpha_module\n",
            encoding="utf-8",
        )

        (root / "pyproject.toml").write_text(
            '[project]\nname="cauldron"\nversion="0.1.0"\n'
            'dependencies=["Django>=5.0"]\n',
            encoding="utf-8",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Integration root fixture import should be allowed without pyproject entry:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Part 1: Dotted-namespace ownership resolution
# ---------------------------------------------------------------------------

def test_private_path_under_dotted_namespace():
    """ARCH002 fires for _-prefixed subpath under a genuinely dotted namespace."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={
                "api.py": "",
                "_internal.py": "X = 1\n",
            },
        )

        _make_fake_package(
            root,
            pkg_name="myapp-consumer",
            slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.api"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "api.py": "from myapp.core._internal import X\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, f"Expected ARCH002 for private path:\n{result.stdout}"
    assert "ARCH002" in result.stdout


def test_non_public_path_under_dotted_namespace():
    """ARCH002 fires for a subpath outside public_api under a dotted namespace."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={
                "api.py": "",
                "internals.py": "X = 1\n",
            },
        )

        _make_fake_package(
            root,
            pkg_name="myapp-consumer",
            slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.api"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "api.py": "from myapp.core.internals import X\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, f"Expected ARCH002 for non-public path:\n{result.stdout}"
    assert "ARCH002" in result.stdout


def test_inbound_import_non_cauldron_namespace():
    """ARCH001 fires even when the imported namespace does not begin with 'cauldron_'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        # Package B imports myapp.core.api WITHOUT declaring the dep
        _make_fake_package(
            root,
            pkg_name="myapp-consumer",
            slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.api"],
            pyproject_deps=[],  # undeclared!
            src_files={
                "api.py": "from myapp.core.api import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH001 for undeclared cross-package non-cauldron import:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_parent_child_namespace_conflict_overlapping():
    """Package A claiming 'myapp' and Package B claiming 'myapp.core' produces OVERLAPPING NAMESPACE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="myapp-root",
            slug="myapp.root",
            namespace="myapp",
            public_api=["myapp.api"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected config error for parent/child namespace overlap:\n{result.stdout}\n{result.stderr}"
    )
    assert "OVERLAPPING NAMESPACE" in (result.stdout + result.stderr), (
        f"Expected OVERLAPPING NAMESPACE in output, got:\n{result.stdout}\n{result.stderr}"
    )


def test_sibling_dotted_namespaces_no_conflict():
    """Package A claiming 'myapp.core' and Package B claiming 'myapp.extensions' is fine."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="myapp-core",
            slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root,
            pkg_name="myapp-extensions",
            slug="myapp.extensions",
            namespace="myapp.extensions",
            public_api=["myapp.extensions.api"],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Sibling dotted namespaces should be allowed:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Part 2: has_package_metadata / unpackaged project modules
# ---------------------------------------------------------------------------

def test_unpackaged_project_module_with_manifest_import_allowed():
    """Unpackaged project module with correct manifest dep is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-core",
            slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        # Project module WITHOUT pyproject.toml
        mod_dir = root / "modules" / "my-module"
        src_ns = mod_dir / "src" / "myapp"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.module", label="My Module",\n'
            '    namespaces=("myapp",),\n'
            '    public_api=("myapp.api",),\n'
            '    requires=(ModuleRequirement(slug="cauldron.core", kind="module"),),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text(
            "from cauldron_core.api import Thing\n",
            encoding="utf-8",
        )
        # NO pyproject.toml in mod_dir

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 0, (
        f"Unpackaged project module with manifest dep should be allowed:\n{result.stdout}"
    )


def test_unpackaged_project_module_without_manifest_dep_rejected():
    """Unpackaged project module without a manifest dep declaration raises ARCH001."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-core",
            slug="cauldron.core",
            namespace="cauldron_core",
            public_api=["cauldron_core.api"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        mod_dir = root / "modules" / "my-module"
        src_ns = mod_dir / "src" / "myapp"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.module", label="My Module",\n'
            '    namespaces=("myapp",),\n'
            '    public_api=("myapp.api",),\n'
            # No requires — should trigger ARCH001
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        (src_ns / "api.py").write_text(
            "from cauldron_core.api import Thing\n",
            encoding="utf-8",
        )
        # NO pyproject.toml

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 1, (
        f"Unpackaged project module without manifest dep should raise ARCH001:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


# ---------------------------------------------------------------------------
# Part 3: Complete dependency-level parity (capabilities + test-only)
# ---------------------------------------------------------------------------

def test_arch001_runtime_optional_dep_missing_manifest_optional():
    """ARCH001 fires when a runtime-optional pyproject dep has no manifest optional= entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-plugin",
            slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": "class X: pass\n"},
        )

        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],
            pyproject_optional_groups={"extra": ["cauldron-plugin"]},
            # No optional= in manifest
            src_files={
                "api.py": "from cauldron_plugin.api import X\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH001 for runtime-optional missing manifest optional=:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_arch004_test_only_dep_in_runtime_manifest():
    """ARCH004 fires when a test-only pyproject dep is claimed as runtime manifest entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-plugin",
            slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": ""},
        )

        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],
            pyproject_optional_groups={"test": ["cauldron-plugin"]},
            optional_requires=[("cauldron.plugin", "module")],
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH004 for test-only dep claimed as runtime manifest requirement:\n{result.stdout}"
    )
    assert "ARCH004" in result.stdout


def test_arch001_test_only_dep_in_production_code():
    """ARCH001 fires when a test-only pyproject dep is imported from production (non-test) code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-plugin",
            slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": "class X: pass\n"},
        )

        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=[],
            pyproject_optional_groups={"test": ["cauldron-plugin"]},
            # Import in src (production), not test file
            src_files={
                "api.py": "from cauldron_plugin.api import X\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH001 for test-only dep imported from production code:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_capability_only_provider_import_raises_arch001():
    """A capability-only relationship does not authorise a direct Python import — ARCH001 fires."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-provider", slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.api"],
            provides=["data.process"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],
            # Only a capability requirement — no kind='module' dep
            requires=[("data.process", "capability")],
            src_files={
                "api.py": "from cauldron_provider.api import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Capability-only dep must not authorise direct import:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_capability_plus_module_dep_import_allowed():
    """A capability requirement plus a separate kind='module' requirement allows direct import."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-provider", slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.api"],
            provides=["data.process"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],
            # Both capability AND module requirements
            requires=[("data.process", "capability"), ("cauldron.provider", "module")],
            src_files={
                "api.py": "from cauldron_provider.api import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Capability + module dep should allow direct import:\n{result.stdout}"
    )


def test_capability_contract_use_without_provider_import_allowed():
    """Consuming a capability via its contract (no provider import) is always allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-provider", slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.api", "cauldron_provider.contracts"],
            provides=["data.process"],
            src_files={
                "api.py": "class Thing: pass\n",
                "contracts.py": "class DataProcessor: pass\n",
            },
        )

        # Consumer with only capability dep; uses cauldron_provider.contracts (public)
        # but we simulate using only the framework's contract registry (no direct provider import).
        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],
            requires=[("data.process", "capability"), ("cauldron.provider", "module")],
            src_files={
                # Uses the public contract path, not a capability_implementation path
                "api.py": "from cauldron_provider.contracts import DataProcessor\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Capability contract import with module dep should be allowed:\n{result.stdout}"
    )


def test_capability_dep_wrong_level_arch004():
    """ARCH004 fires when a main dep's capability is declared as optional= in the manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-provider",
            slug="cauldron.provider",
            namespace="cauldron_provider",
            public_api=["cauldron_provider.api"],
            provides=["data.process"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        _make_fake_package(
            root,
            pkg_name="cauldron-consumer",
            slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-provider"],  # main dep
            optional_requires=[("data.process", "capability")],  # but claimed as optional=
            src_files={"api.py": ""},
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH004 for capability optional= referring to a main-dep provider:\n{result.stdout}"
    )
    assert "ARCH004" in result.stdout


# ---------------------------------------------------------------------------
# Part 4: Guarded optional imports
# ---------------------------------------------------------------------------

def _make_runtime_optional_pair(root: Path, api_source: str) -> None:
    """Helper: build a provider + consumer where consumer has provider as
    runtime-optional dep declared as optional= in manifest."""
    _make_fake_package(
        root,
        pkg_name="cauldron-plugin",
        slug="cauldron.plugin",
        namespace="cauldron_plugin",
        public_api=["cauldron_plugin.api"],
        src_files={"api.py": "class Thing: pass\n"},
    )

    _make_fake_package(
        root,
        pkg_name="cauldron-consumer",
        slug="cauldron.consumer",
        namespace="cauldron_consumer",
        public_api=["cauldron_consumer.api"],
        pyproject_deps=[],
        pyproject_optional_groups={"extra": ["cauldron-plugin"]},
        optional_requires=[("cauldron.plugin", "module")],
        src_files={"api.py": api_source},
    )


def test_guarded_try_except_importerror_allowed():
    """try/except ImportError-wrapped import from a runtime-optional dep is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "try:\n"
            "    from cauldron_plugin.api import Thing\n"
            "except ImportError:\n"
            "    Thing = None\n",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"try/except ImportError guard should be allowed:\n{result.stdout}"
    )


def test_guarded_type_checking_import_allowed():
    """TYPE_CHECKING-guarded import from a runtime-optional dep is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from cauldron_plugin.api import Thing\n",
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"TYPE_CHECKING-guarded import should be allowed:\n{result.stdout}"
    )


def test_function_local_import_without_exception_raises():
    """Import inside a function body without exception handling raises ARCH001."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "def load():\n"
            "    from cauldron_plugin.api import Thing\n"
            "    return Thing\n",
        )
        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Function-local import without exception handling should raise ARCH001:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_function_local_import_with_importerror_handling_allowed():
    """Import inside a function body with explicit ImportError handling is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "def load():\n"
            "    try:\n"
            "        from cauldron_plugin.api import Thing\n"
            "    except ImportError:\n"
            "        return None\n"
            "    return Thing\n",
        )
        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Function-local import with ImportError handling should be allowed:\n{result.stdout}"
    )


def test_bare_except_import_raises():
    """Import inside a bare 'except:' block is not treated as guarded — ARCH001 fires."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "try:\n"
            "    from cauldron_plugin.api import Thing\n"
            "except:\n"
            "    pass\n",
        )
        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Bare-except import should not be treated as guarded:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_unpackaged_optional_unguarded_import_raises():
    """Unpackaged project module with manifest optional= but unguarded import raises ARCH001."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-plugin",
            slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": "class X: pass\n"},
        )

        mod_dir = root / "modules" / "my-module"
        src_ns = mod_dir / "src" / "myapp"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.module", label="My Module",\n'
            '    namespaces=("myapp",),\n'
            '    public_api=("myapp.api",),\n'
            '    optional=(ModuleRequirement(slug="cauldron.plugin", kind="module"),),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        # Unguarded module-level import from optional dep
        (src_ns / "api.py").write_text(
            "from cauldron_plugin.api import X\n",
            encoding="utf-8",
        )
        # NO pyproject.toml

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 1, (
        f"Unpackaged module with manifest optional= but unguarded import should raise ARCH001:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout


def test_unpackaged_optional_guarded_import_allowed():
    """Unpackaged project module with manifest optional= and guarded import is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root,
            pkg_name="cauldron-plugin",
            slug="cauldron.plugin",
            namespace="cauldron_plugin",
            public_api=["cauldron_plugin.api"],
            src_files={"api.py": "class X: pass\n"},
        )

        mod_dir = root / "modules" / "my-module"
        src_ns = mod_dir / "src" / "myapp"
        src_ns.mkdir(parents=True)
        (src_ns / "__init__.py").write_text("", encoding="utf-8")
        (src_ns / "module.py").write_text(
            'from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement\n'
            '_manifest = ModuleManifest(\n'
            '    slug="my.module", label="My Module",\n'
            '    namespaces=("myapp",),\n'
            '    public_api=("myapp.api",),\n'
            '    optional=(ModuleRequirement(slug="cauldron.plugin", kind="module"),),\n'
            ')\n'
            'module = BaseModule(_manifest)\n',
            encoding="utf-8",
        )
        # Guarded import: try/except ImportError
        (src_ns / "api.py").write_text(
            "try:\n"
            "    from cauldron_plugin.api import X\n"
            "except ImportError:\n"
            "    X = None\n",
            encoding="utf-8",
        )
        # NO pyproject.toml

        arch_check = Path(__file__).resolve().parent.parent / "tools" / "arch_check.py"
        result = subprocess.run(
            [sys.executable, str(arch_check), "--root", str(root),
             "--module-root", str(root / "modules")],
            capture_output=True, text=True,
        )

    assert result.returncode == 0, (
        f"Unpackaged module with manifest optional= and guarded import should be allowed:\n{result.stdout}"
    )


def test_unguarded_optional_import_production_raises():
    """Unguarded module-level import from a runtime-optional dep raises ARCH001."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_runtime_optional_pair(
            root,
            "from cauldron_plugin.api import Thing\n",
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Expected ARCH001 for unguarded runtime-optional import:\n{result.stdout}"
    )
    assert "ARCH001" in result.stdout
    assert "Unguarded" in result.stdout


# ---------------------------------------------------------------------------
# Section 4 — Dotted namespace root imports
# ---------------------------------------------------------------------------

def test_dotted_ns_root_import_public_name_allowed():
    """``from myapp.core import api`` where ``myapp.core.api`` is in public_api is allowed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="myapp-core", slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={"api.py": "class Service: pass\n"},
        )

        _make_fake_package(
            root, pkg_name="myapp-consumer", slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.views"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "views.py": "from myapp.core import api\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"from <dotted_ns> import public_name should be allowed:\n{result.stdout}"
    )


def test_dotted_ns_root_import_non_public_name_rejected():
    """``from myapp.core import internals`` where ``myapp.core.internals`` is not in public_api raises ARCH002."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="myapp-core", slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={
                "api.py": "",
                "internals.py": "SECRET = 1\n",
            },
        )

        _make_fake_package(
            root, pkg_name="myapp-consumer", slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.views"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "views.py": "from myapp.core import internals\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"from <dotted_ns> import non_public_name should raise ARCH002:\n{result.stdout}"
    )
    assert "ARCH002" in result.stdout


def test_dotted_ns_root_import_capability_impl_rejected():
    """``from myapp.core import impl`` where ``myapp.core.impl`` is a capability implementation raises ARCH003."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="myapp-core", slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.contracts", "myapp.core.impl"],
            capability_implementations=["myapp.core.impl"],
            provides=["myapp.service"],
            src_files={
                "contracts.py": "class IService: pass\n",
                "impl.py": "class ConcreteService: pass\n",
            },
        )

        _make_fake_package(
            root, pkg_name="myapp-consumer", slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.views"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "views.py": "from myapp.core import impl\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"from <dotted_ns> import capability_impl should raise ARCH003:\n{result.stdout}"
    )
    assert "ARCH003" in result.stdout


# ---------------------------------------------------------------------------
# Section 5 — Empty public_api
# ---------------------------------------------------------------------------

def test_empty_public_api_rejects_any_import():
    """A package with empty public_api rejects all direct cross-module imports (ARCH002)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="cauldron-internal", slug="cauldron.internal",
            namespace="cauldron_internal",
            public_api=[],  # empty — no cross-module import surface
            src_files={"api.py": "class X: pass\n"},
        )

        _make_fake_package(
            root, pkg_name="cauldron-consumer", slug="cauldron.consumer",
            namespace="cauldron_consumer",
            public_api=["cauldron_consumer.api"],
            pyproject_deps=["cauldron-internal"],
            requires=[("cauldron.internal", "module")],
            src_files={
                "api.py": "from cauldron_internal.api import X\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 1, (
        f"Import from empty public_api package should raise ARCH002:\n{result.stdout}"
    )
    assert "ARCH002" in result.stdout


# ---------------------------------------------------------------------------
# Section 6 — Non-cauldron project packages
# ---------------------------------------------------------------------------

def test_project_package_cross_module_allowed():
    """Non-cauldron project packages (e.g. myapp-*) are treated the same as cauldron-* packages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        _make_fake_package(
            root, pkg_name="myapp-core", slug="myapp.core",
            namespace="myapp.core",
            public_api=["myapp.core.api"],
            src_files={"api.py": "class Thing: pass\n"},
        )

        _make_fake_package(
            root, pkg_name="myapp-consumer", slug="myapp.consumer",
            namespace="myapp.consumer",
            public_api=["myapp.consumer.api"],
            pyproject_deps=["myapp-core"],
            requires=[("myapp.core", "module")],
            src_files={
                "api.py": "from myapp.core.api import Thing\n",
            },
        )

        result = _run_arch_check(root)

    assert result.returncode == 0, (
        f"Properly declared non-cauldron cross-module import should pass:\n{result.stdout}"
    )
