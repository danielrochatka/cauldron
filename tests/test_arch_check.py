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
    pyproject_deps: list[str] | None = None,
    src_files: dict[str, str] | None = None,
) -> None:
    """Create a minimal fake Cauldron package in root/packages/<pkg_name>/."""
    pkg_dir = root / "packages" / pkg_name
    src_ns_dir = pkg_dir / "src" / namespace
    src_ns_dir.mkdir(parents=True, exist_ok=True)

    # Write module.py
    deps_str = ", ".join(f'"{d}"' for d in (pyproject_deps or []))
    public_api_str = ", ".join(f'"{p}"' for p in public_api)
    module_py = src_ns_dir / "module.py"
    module_py.write_text(
        f'from cauldron.modules import BaseModule, ModuleManifest\n'
        f'_manifest = ModuleManifest(\n'
        f'    slug="{slug}",\n'
        f'    label="{pkg_name}",\n'
        f'    namespaces=("{namespace}",),\n'
        f'    public_api=({public_api_str},),\n'
        f')\n'
        f'module = BaseModule(_manifest)\n',
        encoding="utf-8",
    )

    # Write __init__.py
    (src_ns_dir / "__init__.py").write_text("", encoding="utf-8")

    # Write extra src files
    for filename, content in (src_files or {}).items():
        (src_ns_dir / filename).write_text(content, encoding="utf-8")

    # Write pyproject.toml
    if pyproject_deps is None:
        pyproject_deps = []
    dep_lines = "\n".join(f'    "cauldron>=0.1.0",' + ("\n" if not pyproject_deps else ""))
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
