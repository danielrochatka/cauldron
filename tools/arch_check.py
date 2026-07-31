"""Architecture checker for the Cauldron monorepo.

Scans all packages in packages/ for cross-boundary import violations.
Can be run as a script or imported as a library.

Exit codes:
    0 — no violations found
    1 — one or more violations found

Rules:
    ARCH001 — Undeclared sibling dependency
    ARCH002 — Private module import from a sibling package
    ARCH003 — Concrete provider bypass (importing *Provider class from sibling)
    ARCH004 — Manifest/pyproject.toml dependency mismatch
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Namespaces that are always allowed without declaration
_PLATFORM_NAMESPACES = frozenset(
    {
        "cauldron",  # core framework (not packages — packages have their own namespaces)
        "django",
    }
)

# Standard library top-level names (representative subset; we exclude by
# checking if the namespace starts with a known cauldron_ prefix instead)
_STDLIB_TOP_LEVEL: frozenset[str] = frozenset()  # handled by _is_cauldron_ns()

_SKIP_DIRS = {"__pycache__", "migrations", ".venv", "node_modules", ".git"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    code: str
    file: Path
    line: int
    message: str
    fix: str
    note: str = ""

    def format(self, root: Path) -> str:
        rel = file_rel(self.file, root)
        lines = [f"{self.code} {rel}:{self.line}"]
        lines.append(f"  {self.message}")
        if self.note:
            lines.append(f"  Note: {self.note}")
        for fix_line in self.fix.splitlines():
            lines.append(f"  Fix: {fix_line}")
        return "\n".join(lines)


@dataclass
class PackageInfo:
    slug: str
    name: str  # pyproject name e.g. "cauldron-content"
    root: Path  # packages/cauldron-content/
    namespaces: list[str]
    public_api: set[str]
    requires_slugs: set[str]  # from manifest requires + optional
    pyproject_deps: set[str]  # cauldron-* packages from pyproject


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _is_cauldron_namespace(ns: str) -> bool:
    """Return True if the namespace looks like a cauldron_* package namespace."""
    return ns.startswith("cauldron_")


def _ns_root(dotted: str) -> str:
    """Return the top-level name from a dotted import path."""
    return dotted.split(".")[0]


def _is_private_segment(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _has_private_segment(dotted: str) -> bool:
    return any(_is_private_segment(seg) for seg in dotted.split("."))


def _cauldron_package_name(namespace: str) -> str:
    """Convert cauldron_foo_bar → cauldron-foo-bar."""
    return namespace.replace("_", "-")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _extract_manifest_fields(module_py: Path) -> dict:
    """AST-parse module.py and extract namespaces, public_api, requires/optional slugs."""
    try:
        source = module_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_py))
    except (SyntaxError, OSError):
        return {}

    result: dict = {
        "namespaces": [],
        "public_api": [],
        "requires_slugs": [],
        "slug": "",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = ""
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr

        if func_name == "ModuleManifest":
            for kw in node.keywords:
                if kw.arg == "slug":
                    if isinstance(kw.value, ast.Constant):
                        result["slug"] = kw.value.value
                elif kw.arg in ("namespaces", "public_api"):
                    tup = kw.value
                    if isinstance(tup, (ast.Tuple, ast.List)):
                        items = []
                        for elt in tup.elts:
                            if isinstance(elt, ast.Constant):
                                items.append(elt.value)
                        result[kw.arg] = items
                elif kw.arg in ("requires", "optional"):
                    tup = kw.value
                    if isinstance(tup, (ast.Tuple, ast.List)):
                        for elt in tup.elts:
                            if isinstance(elt, ast.Call):
                                for req_kw in elt.keywords:
                                    if req_kw.arg == "slug" and isinstance(req_kw.value, ast.Constant):
                                        result["requires_slugs"].append(req_kw.value.value)
                            # also handle positional first arg
                            if isinstance(elt, ast.Call) and elt.args:
                                first = elt.args[0]
                                if isinstance(first, ast.Constant):
                                    result["requires_slugs"].append(first.value)

    return result


def _pyproject_cauldron_deps(pyproject: Path) -> set[str]:
    """Return set of cauldron-* package names from pyproject.toml dependencies."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()

    deps: set[str] = set()
    project = data.get("project", {})
    for dep_str in project.get("dependencies", []):
        # dep_str looks like "cauldron-content>=0.1.0" or just "cauldron-content"
        pkg_name = dep_str.split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0].split(">")[0].split("<")[0].strip()
        if pkg_name.startswith("cauldron-"):
            deps.add(pkg_name)
    # Also check optional-dependencies
    for _extra, extra_deps in project.get("optional-dependencies", {}).items():
        for dep_str in extra_deps:
            pkg_name = dep_str.split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0].split(">")[0].split("<")[0].strip()
            if pkg_name.startswith("cauldron-"):
                deps.add(pkg_name)
    return deps


def _pyproject_package_name(pyproject: Path) -> str:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return data.get("project", {}).get("name", "")


def discover_packages(packages_dir: Path) -> list[PackageInfo]:
    """Find all cauldron-* packages with a module.py."""
    infos: list[PackageInfo] = []

    for pkg_dir in sorted(packages_dir.iterdir()):
        if not pkg_dir.is_dir():
            continue
        src_dir = pkg_dir / "src"
        if not src_dir.exists():
            continue

        # Find module.py files under src/
        module_files = list(src_dir.rglob("module.py"))
        if not module_files:
            continue

        pyproject = pkg_dir / "pyproject.toml"
        pkg_name = _pyproject_package_name(pyproject) if pyproject.exists() else pkg_dir.name
        pyproject_deps = _pyproject_cauldron_deps(pyproject) if pyproject.exists() else set()

        for module_py in module_files:
            fields = _extract_manifest_fields(module_py)
            slug = fields.get("slug", "")
            namespaces = fields.get("namespaces", [])
            public_api = set(fields.get("public_api", []))
            requires_slugs = set(fields.get("requires_slugs", []))

            if not slug and not namespaces:
                continue

            infos.append(
                PackageInfo(
                    slug=slug,
                    name=pkg_name,
                    root=pkg_dir,
                    namespaces=namespaces,
                    public_api=public_api,
                    requires_slugs=requires_slugs,
                    pyproject_deps=pyproject_deps,
                )
            )

    return infos


def build_namespace_maps(packages: list[PackageInfo]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build namespace_owner and module_namespaces maps."""
    namespace_owner: dict[str, str] = {}  # ns -> slug
    module_namespaces: dict[str, list[str]] = {}  # slug -> [ns, ...]

    for pkg in packages:
        module_namespaces[pkg.slug] = list(pkg.namespaces)
        for ns in pkg.namespaces:
            namespace_owner[ns] = pkg.slug

    return namespace_owner, module_namespaces


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

@dataclass
class ImportRef:
    module: str   # the full dotted module name being imported
    line: int
    is_from: bool  # True for "from X import Y"
    names: list[str] = field(default_factory=list)  # names imported (for from X import Y)


def extract_imports(source: str, filename: str = "<unknown>") -> list[ImportRef]:
    """Parse Python source and extract all import statements."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    imports: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportRef(module=alias.name, line=node.lineno, is_from=False))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [alias.name for alias in node.names]
                imports.append(
                    ImportRef(module=node.module, line=node.lineno, is_from=True, names=names)
                )
    return imports


# ---------------------------------------------------------------------------
# Checking logic
# ---------------------------------------------------------------------------

def _owning_package(namespace_root: str, namespace_owner: dict[str, str], packages_by_slug: dict[str, PackageInfo]) -> Optional[PackageInfo]:
    slug = namespace_owner.get(namespace_root)
    if slug:
        return packages_by_slug.get(slug)
    return None


def check_file(
    py_file: Path,
    owning_pkg: PackageInfo,
    namespace_owner: dict[str, str],
    packages_by_slug: dict[str, PackageInfo],
    is_test: bool,
) -> list[Violation]:
    violations: list[Violation] = []

    try:
        source = py_file.read_text(encoding="utf-8")
    except OSError:
        return []

    imports = extract_imports(source, str(py_file))
    owning_ns_set = set(owning_pkg.namespaces)

    for imp in imports:
        ns_root = _ns_root(imp.module)

        # Skip non-cauldron namespaces
        if not _is_cauldron_namespace(ns_root):
            continue

        # Skip imports within own namespace
        if ns_root in owning_ns_set:
            continue

        # Skip platform namespaces (cauldron core, django)
        if ns_root in _PLATFORM_NAMESPACES:
            continue

        # Skip if ns_root is "cauldron" (core framework)
        if ns_root == "cauldron":
            continue

        # Determine the owning package of this import
        import_owner_pkg = _owning_package(ns_root, namespace_owner, packages_by_slug)
        owner_pkg_name = _cauldron_package_name(ns_root)  # e.g. cauldron-content

        # ------------------------------------------------------------------
        # ARCH002 — Private module import
        # ------------------------------------------------------------------
        # Check if any segment (after the root) is private
        full_path = imp.module
        segments = full_path.split(".")
        # segments[0] is the root namespace; check segments[1:] for _private
        private_seg = next(
            (seg for seg in segments[1:] if _is_private_segment(seg)), None
        )
        if private_seg:
            violations.append(
                Violation(
                    code="ARCH002",
                    file=py_file,
                    line=imp.line,
                    message=(
                        f"Import '{imp.module}' accesses a private module in package "
                        f"'{import_owner_pkg.slug if import_owner_pkg else ns_root}'."
                    ),
                    fix="Use the public API instead; private modules are internal implementation details.",
                )
            )
            continue  # still check ARCH001 below if needed — but private violation takes priority

        # ------------------------------------------------------------------
        # ARCH001 — Undeclared sibling dependency
        # ------------------------------------------------------------------
        # The importing package must either have the owning package in its
        # pyproject deps OR be an owner of the namespace itself.
        declared_pkg_names = owning_pkg.pyproject_deps  # set of "cauldron-*" names
        if owner_pkg_name not in declared_pkg_names:
            owner_label = import_owner_pkg.slug if import_owner_pkg else ns_root
            declared_str = ", ".join(sorted(declared_pkg_names)) or "(none)"
            fix_lines = (
                f"Add '{owner_pkg_name}' to [project.dependencies] in "
                f"packages/{owning_pkg.name}/pyproject.toml\n"
                f"       Add ModuleRequirement(slug='{owner_label}') to module.py requires"
            )
            violations.append(
                Violation(
                    code="ARCH001",
                    file=py_file,
                    line=imp.line,
                    message=(
                        f"Import '{imp.module}' is from module '{owner_label}' "
                        f"which is not declared as a dependency."
                    ),
                    fix=fix_lines,
                    note=f"Owner: {owner_label} | Declared deps: {declared_str}",
                )
            )

        # ------------------------------------------------------------------
        # ARCH003 — Concrete provider bypass (only in src/, not tests/ unless flagged)
        # ------------------------------------------------------------------
        # Flag imports of *Provider classes from sibling packages.
        # Exceptions:
        #   - "Fake*Provider" classes from *.testing modules are test helpers
        #     and are allowed (they are not concrete production providers).
        #   - Abstract protocols named *Provider (e.g. AIModelProvider) are
        #     allowed — only concrete implementations are flagged.
        #   - Imports from *.testing modules are explicitly for tests.
        _source_is_testing = imp.module.endswith(".testing") or ".testing." in imp.module
        if imp.is_from and imp.names and not _source_is_testing:
            provider_names = [
                n for n in imp.names
                if n.endswith("Provider")
                and not n.startswith("Fake")
                # Skip abstract protocols / base classes (not "Astro*" or "FlatFile*" etc.)
                and not n.startswith("AI")  # AIModelProvider is a protocol
            ]
            if provider_names:
                for pname in provider_names:
                    note = ""
                    if is_test:
                        note = "test file: prefer a local fake instead of importing the concrete provider"
                    fix = (
                        f"Use the public capability contract instead of importing "
                        f"the concrete '{pname}' class directly from a sibling module."
                    )
                    violations.append(
                        Violation(
                            code="ARCH003",
                            file=py_file,
                            line=imp.line,
                            message=(
                                f"Import of concrete provider '{pname}' from '{imp.module}' "
                                f"bypasses the capability contract."
                            ),
                            fix=fix,
                            note=note,
                        )
                    )

    return violations


def check_arch004(
    pkg: PackageInfo,
    packages_by_slug: dict[str, PackageInfo],
    namespace_owner: dict[str, str],
) -> list[Violation]:
    """ARCH004 — Manifest/pyproject mismatch (manifest-to-pyproject direction only).

    Only checks: if module.py declares a ModuleRequirement with a real module
    slug (e.g. "cauldron.content") but the corresponding package is not in
    pyproject.toml dependencies.

    The reverse direction (pyproject has a dep not explicitly named in manifest
    as a module slug) is NOT flagged, because manifests conventionally use
    capability slugs (e.g. "content.routing") rather than module slugs for
    their runtime dependency declarations.
    """
    violations: list[Violation] = []

    # Deps declared in manifest as module slugs but not in pyproject
    for req_slug in pkg.requires_slugs:
        # Only check slugs that match actual known modules (not capability slugs)
        req_pkg: Optional[PackageInfo] = packages_by_slug.get(req_slug)
        if req_pkg is None:
            # Capability slug (e.g. "content.routing") — skip
            continue
        # It's a real module slug — check that pyproject declares the package
        if req_pkg.name not in pkg.pyproject_deps and req_pkg.name != pkg.name:
            violations.append(
                Violation(
                    code="ARCH004",
                    file=pkg.root / "pyproject.toml",
                    line=0,
                    message=(
                        f"Module slug '{req_slug}' is declared in manifest requires/optional "
                        f"but '{req_pkg.name}' is missing from pyproject.toml dependencies."
                    ),
                    fix=(
                        f"Add '{req_pkg.name}>=0.1.0' to [project.dependencies] in "
                        f"packages/{pkg.name}/pyproject.toml."
                    ),
                )
            )

    return violations


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _find_py_files(directory: Path) -> list[tuple[Path, bool]]:
    """Yield (path, is_test) for all .py files under directory."""
    results: list[tuple[Path, bool]] = []
    if not directory.exists():
        return results
    for py_file in directory.rglob("*.py"):
        if _should_skip(py_file):
            continue
        is_test = "tests" in py_file.parts or py_file.name.startswith("test_")
        results.append((py_file, is_test))
    return results


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

def run_checks(repo_root: Path) -> list[Violation]:
    """Run all architecture checks and return list of violations."""
    packages_dir = repo_root / "packages"
    if not packages_dir.exists():
        print(f"ERROR: packages/ directory not found at {packages_dir}", file=sys.stderr)
        return []

    packages = discover_packages(packages_dir)
    namespace_owner, module_namespaces = build_namespace_maps(packages)
    packages_by_slug = {p.slug: p for p in packages}

    # Build a map from src root namespace → PackageInfo so we can find which
    # package owns a given file
    # namespace → PackageInfo
    ns_to_pkg: dict[str, PackageInfo] = {}
    for pkg in packages:
        for ns in pkg.namespaces:
            ns_to_pkg[ns] = pkg

    # Also build a mapping from src path → PackageInfo based on the path
    # (for files under packages/X/src/ or packages/X/tests/)
    pkg_by_root: dict[Path, PackageInfo] = {p.root: p for p in packages}

    all_violations: list[Violation] = []

    for pkg in packages:
        # Scan src/ and tests/
        for scan_dir in [pkg.root / "src", pkg.root / "tests"]:
            for py_file, is_test in _find_py_files(scan_dir):
                file_violations = check_file(
                    py_file=py_file,
                    owning_pkg=pkg,
                    namespace_owner=namespace_owner,
                    packages_by_slug=packages_by_slug,
                    is_test=is_test,
                )
                all_violations.extend(file_violations)

        # ARCH004 checks
        arch004 = check_arch004(pkg, packages_by_slug, namespace_owner)
        all_violations.extend(arch004)

    return all_violations


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cauldron architecture boundary checker"
    )
    parser.add_argument(
        "--fix-report",
        metavar="FILE",
        help="Write a JSON summary of all violations to FILE",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Repo root directory (default: parent of this script's directory)",
    )
    args = parser.parse_args(argv)

    if args.root:
        repo_root = Path(args.root).resolve()
    else:
        # Default: tools/ is one level below repo root
        repo_root = Path(__file__).resolve().parent.parent

    violations = run_checks(repo_root)

    if violations:
        for v in violations:
            print(v.format(repo_root))
            print()
        print(f"Found {len(violations)} violation(s).")

        if args.fix_report:
            report = [
                {
                    "code": v.code,
                    "file": file_rel(v.file, repo_root),
                    "line": v.line,
                    "message": v.message,
                    "fix": v.fix,
                    "note": v.note,
                }
                for v in violations
            ]
            Path(args.fix_report).write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(f"Fix report written to {args.fix_report}")

        return 1
    else:
        print("No architecture violations found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
