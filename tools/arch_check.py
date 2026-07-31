"""Architecture checker for the Cauldron monorepo.

Scans every package in packages/ and the root src/ directory for cross-boundary
import violations. Can be run as a script or imported as a library.

Exit codes:
    0 — no violations found
    1 — one or more violations found

Rules:
    ARCH001 — Import from a sibling package that is NOT declared in BOTH
               pyproject.toml AND the module.py manifest (requires/optional).
               pyproject.toml alone is insufficient.
    ARCH002 — Import from a non-public path in a sibling package:
               a _-prefixed module, a name imported as "from X import _name",
               or any sub-module path absent from the sibling's declared public_api.
    ARCH003 — Import from a path that the sibling declares as a
               capability_implementation; external callers must use the
               capability contract instead of the concrete provider.
    ARCH004 — Mismatch between pyproject.toml dependencies and manifest
               requires/optional declarations (checked in both directions).
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

# The root cauldron framework package is always allowed — it has no module.py
# in the same format and is the base dependency of everything.
_PLATFORM_PREFIXES = frozenset({"cauldron", "django"})

_SKIP_DIRS = frozenset({"__pycache__", "migrations", ".venv", "node_modules", ".git"})


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
        rel = _rel(self.file, root)
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
    name: str                          # pyproject name, e.g. "cauldron-content"
    root: Path                         # packages/cauldron-content/
    namespaces: list[str]
    public_api: set[str]
    capability_implementations: set[str]
    requires_slugs: set[str]           # ALL slugs from requires + optional (both kinds)
    requires_module_slugs: set[str]    # slugs from kind="module" requirements only
    provides: set[str]                 # capability slugs this module provides
    pyproject_deps: set[str]           # cauldron-* packages from pyproject (req + optional)
    pyproject_main_deps: set[str]      # cauldron-* packages from [project.dependencies] only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _ns_root(dotted: str) -> str:
    return dotted.split(".")[0]


def _is_cauldron_namespace(ns: str) -> bool:
    return ns.startswith("cauldron_")


def _is_platform(ns: str) -> bool:
    return ns in _PLATFORM_PREFIXES


def _is_private_segment(seg: str) -> bool:
    return seg.startswith("_") and not seg.startswith("__")


def _has_private_subpath(dotted: str) -> bool:
    """Return True if any segment after the root is _-prefixed."""
    return any(_is_private_segment(seg) for seg in dotted.split(".")[1:])


def _pkg_name_from_namespace(ns: str) -> str:
    """cauldron_foo_bar → cauldron-foo-bar (approximate pyproject name)."""
    return ns.replace("_", "-")


# ---------------------------------------------------------------------------
# Discovery: AST-based manifest parsing
# ---------------------------------------------------------------------------

def _extract_manifest_fields(module_py: Path) -> dict:
    """AST-parse a module.py and extract key manifest fields without executing code."""
    try:
        source = module_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_py))
    except (SyntaxError, OSError):
        return {}

    result: dict = {
        "slug": "",
        "namespaces": [],
        "public_api": [],
        "capability_implementations": [],
        "requires_slugs": [],
        "requires_module_slugs": [],
        "provides": [],
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name != "ModuleManifest":
            continue

        for kw in node.keywords:
            if kw.arg == "slug" and isinstance(kw.value, ast.Constant):
                result["slug"] = kw.value.value

            elif kw.arg in ("namespaces", "public_api", "capability_implementations"):
                if isinstance(kw.value, (ast.Tuple, ast.List)):
                    result[kw.arg] = [
                        elt.value
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant)
                    ]

            elif kw.arg == "provides":
                if isinstance(kw.value, (ast.Tuple, ast.List)):
                    result["provides"] = [
                        elt.value
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant)
                    ]

            elif kw.arg in ("requires", "optional"):
                if isinstance(kw.value, (ast.Tuple, ast.List)):
                    for elt in kw.value.elts:
                        if not isinstance(elt, ast.Call):
                            continue
                        slug_val = ""
                        kind_val = "module"
                        # keyword args
                        for req_kw in elt.keywords:
                            if req_kw.arg == "slug" and isinstance(req_kw.value, ast.Constant):
                                slug_val = req_kw.value.value
                            elif req_kw.arg == "kind" and isinstance(req_kw.value, ast.Constant):
                                kind_val = req_kw.value.value
                        # positional first arg
                        if not slug_val and elt.args and isinstance(elt.args[0], ast.Constant):
                            slug_val = elt.args[0].value
                        if slug_val:
                            result["requires_slugs"].append(slug_val)
                            if kind_val == "module":
                                result["requires_module_slugs"].append(slug_val)

    return result


def _extract_cauldron_pkg_names(dep_list: list[str]) -> set[str]:
    import re
    result: set[str] = set()
    for dep_str in dep_list:
        pkg_name = re.split(r"[><=!;]", dep_str.strip())[0].strip()
        if pkg_name.startswith("cauldron-") and pkg_name != "cauldron":
            result.add(pkg_name)
    return result


def _pyproject_cauldron_deps(pyproject: Path) -> set[str]:
    """Return cauldron-* package names from ALL sections of pyproject.toml."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()

    project = data.get("project", {})
    deps = _extract_cauldron_pkg_names(project.get("dependencies", []))
    for extra_deps in project.get("optional-dependencies", {}).values():
        deps |= _extract_cauldron_pkg_names(extra_deps)
    return deps


def _pyproject_main_cauldron_deps(pyproject: Path) -> set[str]:
    """Return cauldron-* package names from [project.dependencies] only (not optional)."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()

    project = data.get("project", {})
    return _extract_cauldron_pkg_names(project.get("dependencies", []))


def _pyproject_name(pyproject: Path) -> str:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return data.get("project", {}).get("name", "")


def discover_packages(packages_dir: Path) -> tuple[list[PackageInfo], list[str]]:
    """Find all packages with a module.py and return (packages, config_errors)."""
    infos: list[PackageInfo] = []
    config_errors: list[str] = []
    namespace_seen: dict[str, str] = {}  # ns → first pkg name

    for pkg_dir in sorted(packages_dir.iterdir()):
        if not pkg_dir.is_dir():
            continue
        src_dir = pkg_dir / "src"
        if not src_dir.exists():
            continue

        module_files = list(src_dir.rglob("module.py"))
        if not module_files:
            continue

        pyproject = pkg_dir / "pyproject.toml"
        pkg_name = _pyproject_name(pyproject) if pyproject.exists() else pkg_dir.name
        pyproject_deps = _pyproject_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_main_deps = _pyproject_main_cauldron_deps(pyproject) if pyproject.exists() else set()

        for module_py in module_files:
            fields = _extract_manifest_fields(module_py)
            slug = fields.get("slug", "")
            if not slug and not fields.get("namespaces"):
                continue

            # Detect duplicate namespace ownership
            for ns in fields.get("namespaces", []):
                if ns in namespace_seen:
                    config_errors.append(
                        f"DUPLICATE NAMESPACE: '{ns}' is claimed by both "
                        f"'{namespace_seen[ns]}' and '{pkg_name}'. "
                        f"Each Python namespace must have exactly one owner."
                    )
                else:
                    namespace_seen[ns] = pkg_name

            infos.append(PackageInfo(
                slug=slug,
                name=pkg_name,
                root=pkg_dir,
                namespaces=fields.get("namespaces", []),
                public_api=set(fields.get("public_api", [])),
                capability_implementations=set(fields.get("capability_implementations", [])),
                requires_slugs=set(fields.get("requires_slugs", [])),
                requires_module_slugs=set(fields.get("requires_module_slugs", [])),
                provides=set(fields.get("provides", [])),
                pyproject_deps=pyproject_deps,
                pyproject_main_deps=pyproject_main_deps,
            ))

    return infos, config_errors


def build_namespace_map(packages: list[PackageInfo]) -> dict[str, PackageInfo]:
    """Build namespace → PackageInfo (first-seen wins; duplicates reported by discover_packages)."""
    ns_to_pkg: dict[str, PackageInfo] = {}
    for pkg in packages:
        for ns in pkg.namespaces:
            if ns not in ns_to_pkg:
                ns_to_pkg[ns] = pkg
    return ns_to_pkg


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

@dataclass
class ImportRef:
    module: str          # full dotted module being imported from
    line: int
    is_from: bool
    names: list[str] = field(default_factory=list)  # names in "from X import a, b"


def extract_imports(source: str, filename: str = "<unknown>") -> list[ImportRef]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    imports: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportRef(module=alias.name, line=node.lineno, is_from=False))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [alias.name for alias in node.names]
            imports.append(ImportRef(module=node.module, line=node.lineno, is_from=True, names=names))
    return imports


# ---------------------------------------------------------------------------
# Manifest dependency check helpers
# ---------------------------------------------------------------------------

def _manifest_declares(owning_pkg: PackageInfo, import_pkg: PackageInfo) -> bool:
    """Return True if owning_pkg's manifest declares a dependency on import_pkg.

    Accepted forms:
    - import_pkg.slug appears in owning_pkg.requires_slugs (kind="module")
    - Any capability that import_pkg provides appears in owning_pkg.requires_slugs
    """
    if import_pkg.slug in owning_pkg.requires_slugs:
        return True
    return bool(owning_pkg.requires_slugs & import_pkg.provides)


# ---------------------------------------------------------------------------
# Per-file checking
# ---------------------------------------------------------------------------

def check_file(
    py_file: Path,
    owning_pkg: PackageInfo,
    ns_to_pkg: dict[str, PackageInfo],
    is_test: bool,
) -> list[Violation]:
    # Tests can freely import from private helpers, fixtures, and undeclared-in-manifest
    # packages. Architecture boundary enforcement applies only to shipped source code.
    if is_test:
        return []

    violations: list[Violation] = []

    try:
        source = py_file.read_text(encoding="utf-8")
    except OSError:
        return []

    imports = extract_imports(source, str(py_file))
    own_namespaces = set(owning_pkg.namespaces)

    for imp in imports:
        ns_root = _ns_root(imp.module)

        # Skip non-cauldron and platform namespaces
        if not _is_cauldron_namespace(ns_root):
            continue
        if _is_platform(ns_root):
            continue
        # Skip self-imports
        if ns_root in own_namespaces:
            continue

        import_pkg = ns_to_pkg.get(ns_root)

        # ------------------------------------------------------------------
        # ARCH002a — _-prefixed segment in the module path itself
        # ------------------------------------------------------------------
        if _has_private_subpath(imp.module):
            violations.append(Violation(
                code="ARCH002",
                file=py_file,
                line=imp.line,
                message=f"Import '{imp.module}' accesses a private sub-module of '{ns_root}'.",
                fix="Use the owning package's public API; private modules are implementation details.",
                note=f"Owner: {import_pkg.slug if import_pkg else ns_root}",
            ))
            continue  # ARCH001 not needed when ARCH002 fires

        # ------------------------------------------------------------------
        # ARCH002b — "from cauldron_X import _private_name" pattern
        # ------------------------------------------------------------------
        if imp.is_from:
            private_names = [n for n in imp.names if _is_private_segment(n)]
            for pname in private_names:
                synthetic = f"{imp.module}.{pname}"
                violations.append(Violation(
                    code="ARCH002",
                    file=py_file,
                    line=imp.line,
                    message=f"Import of private name '{pname}' via 'from {imp.module} import {pname}' accesses a private member of '{ns_root}'.",
                    fix=f"Use the public API; '{pname}' is an implementation detail of '{ns_root}'.",
                    note=f"Equivalent private path: {synthetic}",
                ))

        # ------------------------------------------------------------------
        # ARCH002c — Sub-module path absent from sibling's declared public_api
        # ------------------------------------------------------------------
        if import_pkg and import_pkg.public_api and "." in imp.module:
            # Only check sub-module paths (not root namespace imports)
            if imp.module not in import_pkg.public_api:
                violations.append(Violation(
                    code="ARCH002",
                    file=py_file,
                    line=imp.line,
                    message=f"Import '{imp.module}' is not in the declared public_api of '{import_pkg.slug}'.",
                    fix=(
                        f"Only import from paths listed in the public_api of '{import_pkg.slug}'.\n"
                        f"       Public paths: {', '.join(sorted(import_pkg.public_api)) or '(none)'}"
                    ),
                ))
                continue  # ARCH001 is not meaningful if the path itself is non-public

        # ------------------------------------------------------------------
        # ARCH003 — Concrete capability implementation imported by external package
        # ------------------------------------------------------------------
        if import_pkg and import_pkg.capability_implementations:
            if imp.module in import_pkg.capability_implementations:
                violations.append(Violation(
                    code="ARCH003",
                    file=py_file,
                    line=imp.line,
                    message=(
                        f"Import from '{imp.module}' accesses a concrete capability implementation "
                        f"of '{import_pkg.slug}'."
                    ),
                    fix=(
                        f"Use the capability contract instead of the concrete implementation.\n"
                        f"       '{imp.module}' is declared as a capability_implementation — "
                        f"external callers must not import it directly."
                    ),
                    note=f"Find the contract in cauldron_content.site or the relevant framework module.",
                ))
                # Still check ARCH001 below

        # ------------------------------------------------------------------
        # ARCH001 — Import undeclared in BOTH pyproject AND manifest
        # ------------------------------------------------------------------
        pkg_name = import_pkg.name if import_pkg else _pkg_name_from_namespace(ns_root)
        in_pyproject = pkg_name in owning_pkg.pyproject_deps
        in_manifest = import_pkg is not None and _manifest_declares(owning_pkg, import_pkg)

        if not in_pyproject or not in_manifest:
            owner_label = import_pkg.slug if import_pkg else ns_root
            fix_parts = []
            if not in_pyproject:
                fix_parts.append(
                    f"Add '{pkg_name}>=0.1.0' to [project.dependencies] in "
                    f"{_rel(owning_pkg.root / 'pyproject.toml', owning_pkg.root.parent.parent)}"
                )
            if not in_manifest:
                fix_parts.append(
                    f"Add ModuleRequirement(slug='{owner_label}') to requires/optional in "
                    f"{_rel(owning_pkg.root / 'src', owning_pkg.root.parent.parent)}/.../module.py"
                )
            missing = []
            if not in_pyproject:
                missing.append("pyproject.toml")
            if not in_manifest:
                missing.append("module.py manifest")
            violations.append(Violation(
                code="ARCH001",
                file=py_file,
                line=imp.line,
                message=(
                    f"Import '{imp.module}' depends on '{owner_label}' "
                    f"which is undeclared in: {', '.join(missing)}."
                ),
                fix="\n       ".join(fix_parts),
                note=f"pyproject declared: {in_pyproject} | manifest declared: {in_manifest}",
            ))

    return violations


# ---------------------------------------------------------------------------
# ARCH004 — bidirectional pyproject ↔ manifest consistency
# ---------------------------------------------------------------------------

def check_arch004(
    pkg: PackageInfo,
    pkg_by_slug: dict[str, PackageInfo],
    pkg_by_name: dict[str, PackageInfo],
) -> list[Violation]:
    violations: list[Violation] = []

    # Direction A: manifest requires a module slug but pyproject is missing the package
    for req_slug in pkg.requires_module_slugs:
        req_pkg = pkg_by_slug.get(req_slug)
        if req_pkg is None:
            continue  # capability slug or unknown module — skip
        if req_pkg.name not in pkg.pyproject_deps and req_pkg.name != pkg.name:
            violations.append(Violation(
                code="ARCH004",
                file=pkg.root / "pyproject.toml",
                line=0,
                message=(
                    f"Manifest declares kind='module' requirement '{req_slug}' "
                    f"but '{req_pkg.name}' is absent from pyproject.toml dependencies."
                ),
                fix=f"Add '{req_pkg.name}>=0.1.0' to [project.dependencies] in {pkg.name}/pyproject.toml.",
            ))

    # Direction B: pyproject main deps have cauldron-X but manifest makes no mention of it.
    # Only main deps (not optional/test) are checked — optional features and test fixtures
    # don't need manifest declarations since they aren't part of the deployed module contract.
    for dep_name in pkg.pyproject_main_deps:
        if dep_name == pkg.name:
            continue
        dep_pkg = pkg_by_name.get(dep_name)
        if dep_pkg is None:
            continue  # package not discovered (e.g. cauldron-django-auth not in packages/)
        # Check if the manifest mentions dep_pkg at all:
        # - direct slug in requires_slugs, OR
        # - any capability dep_pkg provides in requires_slugs
        if not _manifest_declares(pkg, dep_pkg):
            violations.append(Violation(
                code="ARCH004",
                file=pkg.root / "pyproject.toml",
                line=0,
                message=(
                    f"pyproject.toml declares dependency on '{dep_name}' "
                    f"but the manifest (module.py) has no corresponding requires/optional entry."
                ),
                fix=(
                    f"Add ModuleRequirement(slug='{dep_pkg.slug}') (kind='module') "
                    f"or a matching capability slug to requires/optional in module.py."
                ),
                note=f"'{dep_pkg.slug}' provides: {', '.join(sorted(dep_pkg.provides)) or '(none)'}",
            ))

    return violations


# ---------------------------------------------------------------------------
# Root package scan
# ---------------------------------------------------------------------------

def _root_package_info(repo_root: Path) -> Optional[PackageInfo]:
    """Return a PackageInfo for the root cauldron framework package, if src/ exists."""
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return None
    root_pyproject = repo_root / "pyproject.toml"
    return PackageInfo(
        slug="cauldron",
        name=_pyproject_name(root_pyproject) if root_pyproject.exists() else "cauldron",
        root=repo_root,
        namespaces=["cauldron"],
        public_api=set(),   # root framework is always public to itself
        capability_implementations=set(),
        requires_slugs=set(),
        requires_module_slugs=set(),
        provides=set(),
        pyproject_deps=_pyproject_cauldron_deps(root_pyproject) if root_pyproject.exists() else set(),
        pyproject_main_deps=_pyproject_main_cauldron_deps(root_pyproject) if root_pyproject.exists() else set(),
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _find_py_files(directory: Path) -> list[tuple[Path, bool]]:
    if not directory.exists():
        return []
    result = []
    for py_file in directory.rglob("*.py"):
        if not _should_skip(py_file):
            is_test = "tests" in py_file.parts or py_file.name.startswith("test_")
            result.append((py_file, is_test))
    return result


def run_checks(repo_root: Path) -> tuple[list[Violation], list[str]]:
    """Run all checks. Returns (violations, config_errors)."""
    packages_dir = repo_root / "packages"
    if not packages_dir.exists():
        return [], [f"ERROR: packages/ directory not found at {packages_dir}"]

    packages, config_errors = discover_packages(packages_dir)

    # Include root framework package in scan
    root_pkg = _root_package_info(repo_root)
    all_scan_targets: list[tuple[PackageInfo, list[Path]]] = []
    for pkg in packages:
        dirs = [pkg.root / "src", pkg.root / "tests"]
        all_scan_targets.append((pkg, dirs))
    if root_pkg:
        root_scan_dirs = [repo_root / "src", repo_root / "tests"]
        all_scan_targets.append((root_pkg, root_scan_dirs))

    ns_to_pkg = build_namespace_map(packages)
    # Root package owns "cauldron" (not cauldron_*, those are feature packages)
    # The root namespace is already in _PLATFORM_PREFIXES so won't be flagged.

    pkg_by_slug = {p.slug: p for p in packages}
    pkg_by_name = {p.name: p for p in packages}

    all_violations: list[Violation] = []

    for pkg, scan_dirs in all_scan_targets:
        for scan_dir in scan_dirs:
            for py_file, is_test in _find_py_files(scan_dir):
                all_violations.extend(
                    check_file(py_file, pkg, ns_to_pkg, is_test)
                )

        # ARCH004 — skip root package (it has no manifest requirements to check)
        if pkg.slug != "cauldron":
            all_violations.extend(check_arch004(pkg, pkg_by_slug, pkg_by_name))

    return all_violations, config_errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cauldron architecture boundary checker")
    parser.add_argument("--fix-report", metavar="FILE",
                        help="Write a JSON summary of violations to FILE")
    parser.add_argument("--root", default=None,
                        help="Repo root (default: parent of this script's directory)")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent

    violations, config_errors = run_checks(repo_root)

    if config_errors:
        for err in config_errors:
            print(f"CONFIG ERROR: {err}", file=sys.stderr)
        return 1

    if violations:
        for v in violations:
            print(v.format(repo_root))
            print()
        print(f"Found {len(violations)} violation(s).")

        if args.fix_report:
            report = [
                {"code": v.code, "file": _rel(v.file, repo_root),
                 "line": v.line, "message": v.message, "fix": v.fix, "note": v.note}
                for v in violations
            ]
            Path(args.fix_report).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Fix report written to {args.fix_report}")
        return 1

    print("No architecture violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
