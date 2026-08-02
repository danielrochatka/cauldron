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
    name: str                                   # pyproject name, e.g. "cauldron-content"
    root: Path                                  # packages/cauldron-content/
    namespaces: list[str]
    public_api: set[str]
    capability_implementations: set[str]
    requires_slugs: set[str]                    # ALL slugs from requires + optional (both kinds)
    requires_module_slugs: set[str]             # slugs from kind="module" requirements only (both requires= and optional=)
    provides: set[str]                          # capability slugs this module provides
    pyproject_deps: set[str]                    # cauldron-* packages from pyproject (req + optional)
    pyproject_main_deps: set[str]               # cauldron-* packages from [project.dependencies] only
    pyproject_optional_deps: set[str]           # cauldron-* only in optional-deps, NOT in main
    manifest_requires_module_slugs: set[str]    # kind='module' from requires= only
    manifest_optional_module_slugs: set[str]    # kind='module' from optional= only
    pyproject_test_deps: set[str]               # packages ONLY in [project.optional-dependencies].test
    pyproject_runtime_optional_deps: set[str]   # packages in other optional groups (not test), NOT in main deps
    has_module_manifest: bool = True            # True when a real module.py was found


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


def _under_prefix(path: str, prefixes: set[str]) -> bool:
    """True if *path* equals any prefix or is a dotted descendant of one.

    ``cauldron_x.contracts`` covers ``cauldron_x.contracts.types`` but not
    ``cauldron_x.contracts_extra``.
    """
    return any(path == p or path.startswith(p + ".") for p in prefixes)


def _pkg_name_from_namespace(ns: str) -> str:
    """cauldron_foo_bar → cauldron-foo-bar (approximate pyproject name)."""
    return ns.replace("_", "-")


def _find_owner(import_path: str, ns_to_pkg: dict[str, PackageInfo]) -> PackageInfo | None:
    """Return the PackageInfo that owns *import_path*, using boundary-aware longest-prefix matching."""
    best_ns: str | None = None
    for ns in ns_to_pkg:
        if import_path == ns or import_path.startswith(ns + "."):
            if best_ns is None or len(ns) > len(best_ns):
                best_ns = ns
    return ns_to_pkg[best_ns] if best_ns else None


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
        "manifest_requires_module_slugs": [],
        "manifest_optional_module_slugs": [],
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
                is_optional = (kw.arg == "optional")
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
                                if is_optional:
                                    result["manifest_optional_module_slugs"].append(slug_val)
                                else:
                                    result["manifest_requires_module_slugs"].append(slug_val)

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


def _pyproject_optional_only_cauldron_deps(pyproject: Path) -> set[str]:
    """Packages in optional-deps but NOT in main [project.dependencies]."""
    all_deps = _pyproject_cauldron_deps(pyproject)
    main_deps = _pyproject_main_cauldron_deps(pyproject)
    return all_deps - main_deps


def _pyproject_test_cauldron_deps(pyproject: Path) -> set[str]:
    """Packages in the 'test' optional group ONLY (not in main deps)."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    project = data.get("project", {})
    test_deps = _extract_cauldron_pkg_names(
        project.get("optional-dependencies", {}).get("test", [])
    )
    main_deps = _extract_cauldron_pkg_names(project.get("dependencies", []))
    return test_deps - main_deps


def _pyproject_runtime_optional_cauldron_deps(pyproject: Path) -> set[str]:
    """Packages in optional groups OTHER than 'test', not in main deps."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    project = data.get("project", {})
    main_deps = _extract_cauldron_pkg_names(project.get("dependencies", []))
    opt_groups = project.get("optional-dependencies", {})
    runtime_optional: set[str] = set()
    for group_name, group_deps in opt_groups.items():
        if group_name == "test":
            continue
        runtime_optional |= _extract_cauldron_pkg_names(group_deps)
    return runtime_optional - main_deps


def _pyproject_name(pyproject: Path) -> str:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return data.get("project", {}).get("name", "")


def _validate_api_paths(
    namespaces: list[str],
    public_api: list[str],
    capability_implementations: list[str],
    pkg_label: str,
    config_errors: list[str],
) -> None:
    """Validate that public_api and capability_implementations paths are under owned namespaces."""
    for path in public_api:
        if not any(path == ns or path.startswith(ns + ".") for ns in namespaces):
            config_errors.append(
                f"BAD public_api: '{path}' in '{pkg_label}' is not under any owned namespace "
                f"({', '.join(namespaces) or '(none)'})."
            )
    for path in capability_implementations:
        if not any(path == ns or path.startswith(ns + ".") for ns in namespaces):
            config_errors.append(
                f"BAD capability_implementations: '{path}' in '{pkg_label}' is not under any owned namespace "
                f"({', '.join(namespaces) or '(none)'})."
            )


def _register_namespace(
    ns: str,
    pkg_name: str,
    namespace_seen: dict[str, str],
    config_errors: list[str],
) -> None:
    """Register a namespace, checking for exact duplicates and parent/child overlaps."""
    if ns in namespace_seen:
        config_errors.append(
            f"DUPLICATE NAMESPACE: '{ns}' is claimed by both "
            f"'{namespace_seen[ns]}' and '{pkg_name}'. "
            f"Each Python namespace must have exactly one owner."
        )
    else:
        # Check for parent/child overlap with existing namespaces
        for existing_ns, existing_pkg in list(namespace_seen.items()):
            if existing_pkg == pkg_name:
                continue
            if existing_ns.startswith(ns + ".") or ns.startswith(existing_ns + "."):
                config_errors.append(
                    f"OVERLAPPING NAMESPACE: '{ns}' (in '{pkg_name}') and "
                    f"'{existing_ns}' (in '{existing_pkg}') have a parent/child "
                    f"relationship; ownership is ambiguous."
                )
        namespace_seen[ns] = pkg_name


def discover_packages(
    packages_dir: Path,
    namespace_seen: Optional[dict[str, str]] = None,
) -> tuple[list[PackageInfo], list[str]]:
    """Find all packages with a module.py and return (packages, config_errors)."""
    infos: list[PackageInfo] = []
    config_errors: list[str] = []
    if namespace_seen is None:
        namespace_seen = {}  # ns → first pkg name

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
        pyproject_optional_deps = _pyproject_optional_only_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_test_deps = _pyproject_test_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_runtime_optional_deps = _pyproject_runtime_optional_cauldron_deps(pyproject) if pyproject.exists() else set()

        for module_py in module_files:
            fields = _extract_manifest_fields(module_py)
            slug = fields.get("slug", "")
            if not slug and not fields.get("namespaces"):
                continue

            namespaces = fields.get("namespaces", [])
            public_api = fields.get("public_api", [])
            cap_impls = fields.get("capability_implementations", [])

            # Detect duplicate and overlapping namespace ownership
            for ns in namespaces:
                _register_namespace(ns, pkg_name, namespace_seen, config_errors)

            # Validate public_api and capability_implementations belong to owned namespaces
            _validate_api_paths(namespaces, public_api, cap_impls, pkg_name, config_errors)

            infos.append(PackageInfo(
                slug=slug,
                name=pkg_name,
                root=pkg_dir,
                namespaces=namespaces,
                public_api=set(public_api),
                capability_implementations=set(cap_impls),
                requires_slugs=set(fields.get("requires_slugs", [])),
                requires_module_slugs=set(fields.get("requires_module_slugs", [])),
                provides=set(fields.get("provides", [])),
                pyproject_deps=pyproject_deps,
                pyproject_main_deps=pyproject_main_deps,
                pyproject_optional_deps=pyproject_optional_deps,
                manifest_requires_module_slugs=set(fields.get("manifest_requires_module_slugs", [])),
                manifest_optional_module_slugs=set(fields.get("manifest_optional_module_slugs", [])),
                pyproject_test_deps=pyproject_test_deps,
                pyproject_runtime_optional_deps=pyproject_runtime_optional_deps,
                has_module_manifest=True,
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

def _manifest_declares_module(owning_pkg: PackageInfo, import_pkg: PackageInfo) -> bool:
    """Return True if owning_pkg has an explicit kind='module' dep on import_pkg.

    Only kind='module' requirements authorize direct Python imports from
    a sibling package. Capability requirements (kind='capability') authorize
    using the runtime capability contract, not static imports.
    """
    return import_pkg.slug in owning_pkg.requires_module_slugs


def _manifest_declares(owning_pkg: PackageInfo, import_pkg: PackageInfo) -> bool:
    """Return True if owning_pkg's manifest mentions import_pkg in any form.

    Used by ARCH004 direction-B: if the dep appears anywhere in the manifest
    (module OR capability) the pair is consistent. A separate ARCH001 check
    enforces that direct imports specifically need kind='module'.
    """
    if import_pkg.slug in owning_pkg.requires_slugs:
        return True
    return bool(owning_pkg.requires_slugs & import_pkg.provides)


# ---------------------------------------------------------------------------
# ARCH003 helper — checks both exact paths and root-namespace from-imports
# ---------------------------------------------------------------------------

def _arch003_violations(
    imp: "ImportRef",
    import_pkg: "Optional[PackageInfo]",
    py_file: Path,
) -> "list[Violation]":
    """Return ARCH003 violations for a single import statement.

    Checks two forms:
    - Exact path:  ``import cauldron_x.impl`` / ``from cauldron_x.impl import …``
    - Root import: ``from cauldron_x import impl`` where ``cauldron_x.impl`` is
      in capability_implementations (bypasses the dotted-path check).
    """
    if not import_pkg or not import_pkg.capability_implementations:
        return []

    violations: list[Violation] = []
    cap_impls = import_pkg.capability_implementations

    def _fire(path: str) -> None:
        violations.append(Violation(
            code="ARCH003",
            file=py_file,
            line=imp.line,
            message=(
                f"Import from '{path}' accesses a concrete capability implementation "
                f"of '{import_pkg.slug}'."
            ),
            fix=(
                f"Use the capability contract instead of the concrete implementation.\n"
                f"       '{path}' is declared as a capability_implementation — "
                f"external callers must not import it directly."
            ),
        ))

    # Exact sub-module path: ``import cauldron_x.impl`` or ``from cauldron_x.impl import …``
    for impl in cap_impls:
        if imp.module == impl or imp.module.startswith(impl + "."):
            _fire(imp.module)
            break

    # Root-namespace from-import: ``from cauldron_x import impl``
    # Construct synthetic path and check against capability_implementations.
    if imp.is_from and "." not in imp.module:
        for name in imp.names:
            if name.startswith("_"):
                continue  # private names handled by ARCH002b
            synthetic = f"{imp.module}.{name}"
            if any(synthetic == impl or synthetic.startswith(impl + ".") for impl in cap_impls):
                _fire(synthetic)

    return violations


# ---------------------------------------------------------------------------
# Per-file checking
# ---------------------------------------------------------------------------

def check_file(
    py_file: Path,
    owning_pkg: PackageInfo,
    ns_to_pkg: dict[str, PackageInfo],
    is_test: bool,
) -> list[Violation]:
    try:
        source = py_file.read_text(encoding="utf-8")
    except OSError:
        return []

    imports = extract_imports(source, str(py_file))
    own_namespaces = set(owning_pkg.namespaces)
    violations: list[Violation] = []

    for imp in imports:
        ns_root = _ns_root(imp.module)

        if not _is_cauldron_namespace(ns_root):
            continue
        if _is_platform(ns_root):
            continue
        if _under_prefix(imp.module, own_namespaces):
            continue

        import_pkg = _find_owner(imp.module, ns_to_pkg)

        # ------------------------------------------------------------------
        # ARCH003 — runs for BOTH test and source files.
        # Tests may import private helpers and undeclared deps freely, but
        # must not hardcode concrete capability implementations (that is the
        # drift path ARCH003 is designed to prevent regardless of context).
        # ------------------------------------------------------------------
        violations.extend(_arch003_violations(imp, import_pkg, py_file))

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
        if import_pkg and import_pkg.public_api:
            if "." in imp.module:
                # Dotted path: validate directly
                if not _under_prefix(imp.module, import_pkg.public_api):
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
            elif imp.is_from:
                # Root-namespace from-import: ``from cauldron_x import name``
                # Each imported name synthesizes a sub-module path for validation.
                for name in imp.names:
                    if name.startswith("_"):
                        continue  # already handled by ARCH002b
                    synthetic = f"{imp.module}.{name}"
                    if not _under_prefix(synthetic, import_pkg.public_api):
                        violations.append(Violation(
                            code="ARCH002",
                            file=py_file,
                            line=imp.line,
                            message=(
                                f"'from {imp.module} import {name}' resolves to '{synthetic}' "
                                f"which is not in the declared public_api of '{import_pkg.slug}'."
                            ),
                            fix=(
                                f"Only import from paths listed in the public_api of '{import_pkg.slug}'.\n"
                                f"       Public paths: {', '.join(sorted(import_pkg.public_api)) or '(none)'}"
                            ),
                        ))

        # ------------------------------------------------------------------
        # ARCH001 — Direct import not declared in BOTH pyproject AND manifest.
        # Manifest check requires kind='module'; capability deps authorize the
        # runtime contract only, not static Python imports.
        # ------------------------------------------------------------------
        pkg_name = import_pkg.name if import_pkg else _pkg_name_from_namespace(ns_root)
        owner_label = import_pkg.slug if import_pkg else ns_root

        in_main = pkg_name in owning_pkg.pyproject_main_deps
        in_runtime_opt = pkg_name in owning_pkg.pyproject_runtime_optional_deps
        in_test_only = pkg_name in owning_pkg.pyproject_test_deps
        in_manifest_requires = import_pkg is not None and import_pkg.slug in owning_pkg.manifest_requires_module_slugs
        in_manifest_optional = import_pkg is not None and import_pkg.slug in owning_pkg.manifest_optional_module_slugs
        in_manifest_any = in_manifest_requires or in_manifest_optional

        if is_test and not owning_pkg.has_module_manifest:
            # Framework integration root (no module.py): its tests/ is an integration test
            # suite for the whole framework. Cauldron namespace imports are allowed without
            # pyproject declarations — the repo checkout is the declaration.
            pass  # allowed
        elif is_test and in_test_only:
            # Test-only dep (only in test extra, no manifest needed)
            pass  # allowed — no ARCH001
        elif in_main and in_manifest_requires:
            pass  # correct: main dep declared as requires= module
        elif in_runtime_opt and in_manifest_optional:
            pass  # correct: runtime-optional dep declared as optional= module
        elif in_main and in_manifest_optional:
            # Misplaced: main dep satisfied by optional= manifest entry
            violations.append(Violation(
                code="ARCH001",
                file=py_file,
                line=imp.line,
                message=(
                    f"Import '{imp.module}' from '{owner_label}' is a main dependency "
                    f"but its module requirement is under manifest optional= instead of requires=."
                ),
                fix=(
                    f"Move ModuleRequirement(slug='{owner_label}', kind='module') from "
                    f"optional= to requires= in module.py."
                ),
                note=f"'{pkg_name}' is in main [project.dependencies]; manifest requires= is required.",
            ))
        elif in_runtime_opt and in_manifest_requires:
            # Misplaced: runtime-optional dep declared under requires= (would force it as a required dep)
            violations.append(Violation(
                code="ARCH001",
                file=py_file,
                line=imp.line,
                message=(
                    f"Import '{imp.module}' from '{owner_label}' is a runtime-optional dependency "
                    f"but its module requirement is under manifest requires= instead of optional=."
                ),
                fix=(
                    f"Move ModuleRequirement(slug='{owner_label}', kind='module') from "
                    f"requires= to optional= in module.py, or promote the package to main [project.dependencies]."
                ),
                note=f"'{pkg_name}' is only in a non-test optional-dependency group.",
            ))
        else:
            # Missing declarations
            fix_parts = []
            missing = []
            if not (in_main or in_runtime_opt or in_test_only):
                fix_parts.append(
                    f"Add '{pkg_name}>=0.1.0' to [project.dependencies] in "
                    f"{_rel(owning_pkg.root / 'pyproject.toml', owning_pkg.root.parent.parent)}"
                )
                missing.append("pyproject.toml")
            if not in_manifest_any:
                fix_parts.append(
                    f"Add ModuleRequirement(slug='{owner_label}', kind='module') to requires/optional in "
                    f"{_rel(owning_pkg.root / 'src', owning_pkg.root.parent.parent)}/.../module.py"
                )
                missing.append("module.py manifest (kind='module' required)")
            if missing:
                violations.append(Violation(
                    code="ARCH001",
                    file=py_file,
                    line=imp.line,
                    message=(
                        f"Import '{imp.module}' depends on '{owner_label}' "
                        f"which is undeclared in: {', '.join(missing)}."
                    ),
                    fix="\n       ".join(fix_parts),
                    note=f"pyproject main: {in_main} | runtime-opt: {in_runtime_opt} | test-only: {in_test_only} | manifest: {in_manifest_any}",
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

    # Direction A additional: manifest requires_module_slugs must correspond to main deps
    for req_slug in pkg.manifest_requires_module_slugs:
        req_pkg = pkg_by_slug.get(req_slug)
        if req_pkg is None:
            continue
        if req_pkg.name in pkg.pyproject_runtime_optional_deps:
            violations.append(Violation(
                code="ARCH004",
                file=pkg.root / "pyproject.toml",
                line=0,
                message=(
                    f"Manifest requires= declares kind='module' for '{req_slug}' "
                    f"but '{req_pkg.name}' is only a runtime-optional pyproject dependency, not a main dep."
                ),
                fix=(
                    f"Either promote '{req_pkg.name}' to main [project.dependencies] "
                    f"or move its ModuleRequirement to optional= in module.py."
                ),
            ))

    # Direction A: manifest optional_module_slugs must correspond to runtime-optional deps
    for opt_slug in pkg.manifest_optional_module_slugs:
        opt_pkg = pkg_by_slug.get(opt_slug)
        if opt_pkg is None:
            continue
        if opt_pkg.name in pkg.pyproject_main_deps:
            violations.append(Violation(
                code="ARCH004",
                file=pkg.root / "pyproject.toml",
                line=0,
                message=(
                    f"Manifest optional= declares kind='module' for '{opt_slug}' "
                    f"but '{opt_pkg.name}' is a main pyproject dependency, not runtime-optional."
                ),
                fix=(
                    f"Move ModuleRequirement(slug='{opt_slug}', kind='module') from optional= to requires= in module.py."
                ),
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
# Root package scan (configurable — works for core framework and instances)
# ---------------------------------------------------------------------------

def _root_package_infos(
    repo_root: Path,
    config_errors: list[str],
) -> list[PackageInfo]:
    """Discover module PackageInfos from the repo-root src/ directory.

    Scans src/ for module.py files (supporting project-owned module roots in
    Cauldron instances alongside the core framework). If no module.py is found
    (e.g. the Cauldron framework itself), a synthetic bare entry is created so
    outgoing imports from src/ and tests/ are still checked.
    """
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []

    root_pyproject = repo_root / "pyproject.toml"
    pyproject_deps = _pyproject_cauldron_deps(root_pyproject) if root_pyproject.exists() else set()
    pyproject_main_deps = _pyproject_main_cauldron_deps(root_pyproject) if root_pyproject.exists() else set()
    pyproject_optional_deps = _pyproject_optional_only_cauldron_deps(root_pyproject) if root_pyproject.exists() else set()
    pyproject_test_deps = _pyproject_test_cauldron_deps(root_pyproject) if root_pyproject.exists() else set()
    pyproject_runtime_optional_deps = _pyproject_runtime_optional_cauldron_deps(root_pyproject) if root_pyproject.exists() else set()
    pkg_name = _pyproject_name(root_pyproject) if root_pyproject.exists() else "cauldron"

    infos: list[PackageInfo] = []
    for module_py in sorted(src_dir.rglob("module.py")):
        if _should_skip(module_py):
            continue
        fields = _extract_manifest_fields(module_py)
        slug = fields.get("slug", "")
        if not slug:
            continue

        namespaces = fields.get("namespaces", [])
        public_api = fields.get("public_api", [])
        cap_impls = fields.get("capability_implementations", [])

        _validate_api_paths(namespaces, public_api, cap_impls, pkg_name, config_errors)

        infos.append(PackageInfo(
            slug=slug,
            name=pkg_name,
            root=repo_root,
            namespaces=namespaces,
            public_api=set(public_api),
            capability_implementations=set(cap_impls),
            requires_slugs=set(fields.get("requires_slugs", [])),
            requires_module_slugs=set(fields.get("requires_module_slugs", [])),
            provides=set(fields.get("provides", [])),
            pyproject_deps=pyproject_deps,
            pyproject_main_deps=pyproject_main_deps,
            pyproject_optional_deps=pyproject_optional_deps,
            manifest_requires_module_slugs=set(fields.get("manifest_requires_module_slugs", [])),
            manifest_optional_module_slugs=set(fields.get("manifest_optional_module_slugs", [])),
            pyproject_test_deps=pyproject_test_deps,
            pyproject_runtime_optional_deps=pyproject_runtime_optional_deps,
            has_module_manifest=True,
        ))

    if not infos:
        # No module.py — synthesise a root package so src/ is still scanned.
        # Determine the primary namespace from the first package dir in src/.
        primary_ns = "cauldron"
        for child in sorted(src_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and (child / "__init__.py").exists():
                primary_ns = child.name
                break
        infos.append(PackageInfo(
            slug=pkg_name,
            name=pkg_name,
            root=repo_root,
            namespaces=[primary_ns],
            public_api=set(),
            capability_implementations=set(),
            requires_slugs=set(),
            requires_module_slugs=set(),
            provides=set(),
            pyproject_deps=pyproject_deps,
            pyproject_main_deps=pyproject_main_deps,
            pyproject_optional_deps=pyproject_optional_deps,
            manifest_requires_module_slugs=set(),
            manifest_optional_module_slugs=set(),
            pyproject_test_deps=pyproject_test_deps,
            pyproject_runtime_optional_deps=pyproject_runtime_optional_deps,
            has_module_manifest=False,
        ))

    return infos


# ---------------------------------------------------------------------------
# Project module discovery
# ---------------------------------------------------------------------------

def discover_project_modules(
    module_root: Path,
    config_errors: list[str],
    namespace_seen: dict[str, str],
) -> list[PackageInfo]:
    """Discover project-owned modules under *module_root*.

    Each immediate subdirectory of *module_root* that contains a ``src/``
    directory with a ``module.py`` is treated as a project-owned module.
    These participate in namespace-ownership detection and are validated
    with the same rules as packaged modules.

    *namespace_seen* is shared with the caller so duplicate namespace
    detection spans both packaged and project-owned modules.
    """
    infos: list[PackageInfo] = []
    if not module_root.exists():
        config_errors.append(
            f"PROJECT MODULE ROOT does not exist: '{module_root}'. "
            "Check your --module-root argument."
        )
        return infos

    for mod_dir in sorted(module_root.iterdir()):
        if not mod_dir.is_dir():
            continue
        src_dir = mod_dir / "src"
        if not src_dir.exists():
            continue

        module_files = list(src_dir.rglob("module.py"))
        if not module_files:
            continue

        pyproject = mod_dir / "pyproject.toml"
        pkg_name = _pyproject_name(pyproject) if pyproject.exists() else mod_dir.name
        pyproject_deps = _pyproject_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_main_deps = _pyproject_main_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_optional_deps = _pyproject_optional_only_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_test_deps = _pyproject_test_cauldron_deps(pyproject) if pyproject.exists() else set()
        pyproject_runtime_optional_deps = _pyproject_runtime_optional_cauldron_deps(pyproject) if pyproject.exists() else set()

        for module_py in module_files:
            fields = _extract_manifest_fields(module_py)
            slug = fields.get("slug", "")
            if not slug and not fields.get("namespaces"):
                continue

            namespaces = fields.get("namespaces", [])
            public_api = fields.get("public_api", [])
            cap_impls = fields.get("capability_implementations", [])

            for ns in namespaces:
                _register_namespace(ns, pkg_name, namespace_seen, config_errors)

            _validate_api_paths(namespaces, public_api, cap_impls, pkg_name, config_errors)

            infos.append(PackageInfo(
                slug=slug,
                name=pkg_name,
                root=mod_dir,
                namespaces=namespaces,
                public_api=set(public_api),
                capability_implementations=set(cap_impls),
                requires_slugs=set(fields.get("requires_slugs", [])),
                requires_module_slugs=set(fields.get("requires_module_slugs", [])),
                provides=set(fields.get("provides", [])),
                pyproject_deps=pyproject_deps,
                pyproject_main_deps=pyproject_main_deps,
                pyproject_optional_deps=pyproject_optional_deps,
                manifest_requires_module_slugs=set(fields.get("manifest_requires_module_slugs", [])),
                manifest_optional_module_slugs=set(fields.get("manifest_optional_module_slugs", [])),
                pyproject_test_deps=pyproject_test_deps,
                pyproject_runtime_optional_deps=pyproject_runtime_optional_deps,
                has_module_manifest=True,
            ))

    return infos


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


def run_checks(
    repo_root: Path,
    project_module_roots: list[Path] | None = None,
) -> tuple[list[Violation], list[str]]:
    """Run all checks. Returns (violations, config_errors)."""
    packages_dir = repo_root / "packages"
    config_errors: list[str] = []
    namespace_seen: dict[str, str] = {}

    packages: list[PackageInfo] = []
    if packages_dir.exists():
        discovered, pkg_errors = discover_packages(packages_dir, namespace_seen)
        packages = discovered
        config_errors.extend(pkg_errors)

    # Discover project-owned modules from any extra module roots.
    project_pkgs: list[PackageInfo] = []
    for mod_root in (project_module_roots or []):
        proj_infos = discover_project_modules(mod_root, config_errors, namespace_seen)
        project_pkgs.extend(proj_infos)

    # Discover project-owned module roots from src/ (supports both the core
    # framework and configurable Cauldron instances with their own modules).
    root_pkgs = _root_package_infos(repo_root, config_errors)

    all_scan_targets: list[tuple[PackageInfo, list[Path]]] = []
    for pkg in packages:
        dirs = [pkg.root / "src", pkg.root / "tests"]
        all_scan_targets.append((pkg, dirs))
    for proj_pkg in project_pkgs:
        dirs = [proj_pkg.root / "src", proj_pkg.root / "tests"]
        all_scan_targets.append((proj_pkg, dirs))
    for root_pkg in root_pkgs:
        all_scan_targets.append((root_pkg, [repo_root / "src", repo_root / "tests"]))

    all_known_packages = packages + project_pkgs
    ns_to_pkg = build_namespace_map(all_known_packages)
    # Root namespace(s) are in _PLATFORM_PREFIXES so they won't be flagged as
    # cross-boundary imports.

    pkg_by_slug = {p.slug: p for p in all_known_packages}
    pkg_by_name = {p.name: p for p in all_known_packages}

    all_violations: list[Violation] = []

    for pkg, scan_dirs in all_scan_targets:
        for scan_dir in scan_dirs:
            for py_file, is_test in _find_py_files(scan_dir):
                all_violations.extend(
                    check_file(py_file, pkg, ns_to_pkg, is_test)
                )

        # ARCH004 — run for packages/ packages and project modules with pyproject.toml
        if pkg in packages or (pkg in project_pkgs and (pkg.root / "pyproject.toml").exists()):
            all_violations.extend(check_arch004(pkg, pkg_by_slug, pkg_by_name))

    return all_violations, config_errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cauldron architecture boundary checker")
    parser.add_argument("--fix-report", metavar="FILE",
                        help="Write a JSON summary of violations to FILE")
    parser.add_argument("--root", default=None,
                        help="Repo root (default: parent of this script's directory)")
    parser.add_argument("--module-root", action="append", default=[],
                        metavar="DIR",
                        help="Path to a directory of project-owned module subdirectories (may be repeated)")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    project_roots = [Path(r).resolve() for r in args.module_root]

    violations, config_errors = run_checks(repo_root, project_module_roots=project_roots or None)

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
