# Cauldron Architecture

This document describes the architectural boundaries and conventions enforced
across the Cauldron monorepo.

---

## 1. Module Boundaries

Each package in `packages/cauldron-*/` owns one or more Python namespaces,
declared in its `ModuleManifest.namespaces` field. A package may only import
from a sibling namespace if:

1. The sibling package is listed in `[project.dependencies]` in its
   `pyproject.toml`, **and**
2. The corresponding `ModuleRequirement` is declared in the manifest
   `requires` or `optional` field.

Both declarations must be kept in sync. The architecture checker (see below)
enforces this at CI time.

**Platform namespaces** (`cauldron` core, `django`) are always allowed and
do not need explicit declarations.

---

## 2. Public API Contract

Each package exposes a set of public import paths via `ModuleManifest.public_api`.
Consumers of a package **must** only import from paths listed in `public_api`.

**Rule:** Never import from a module whose name starts with `_` (e.g.
`cauldron_content._identifiers`) from a different owning package.
Private modules are internal implementation details and may change without
notice.

**Correct:**
```python
from cauldron_content.contracts import validate_identifier_segment
```

**Incorrect:**
```python
from cauldron_content._identifiers import validate_identifier_segment  # ARCH002
```

---

## 3. Capability Contracts

When a capability is provided through a contract/interface, use the contract
rather than importing the concrete provider implementation directly.

For example, the public URL capability is accessed via:
```python
from cauldron_content.site import get_public_url
```

Not via the concrete provider:
```python
from cauldron_site_astro.public_url import AstroPublicUrlProvider  # ARCH003
```

This ensures that swapping provider implementations does not break consumers.

**In tests:** Prefer defining a local fake/stub that implements the same
interface rather than importing the concrete provider from a sibling package.
This keeps tests isolated and avoids cross-boundary coupling.

---

## 4. Declaring Dependencies

When adding a dependency on a sibling Cauldron package, **both** files must
be updated together:

1. **`pyproject.toml`** — add the package to `[project.dependencies]`:
   ```toml
   dependencies = [
       "cauldron-content>=0.1.0",
   ]
   ```

2. **`module.py`** — add a `ModuleRequirement` to the manifest:
   ```python
   requires=(
       ModuleRequirement(slug="cauldron.content"),
   ),
   ```

For truly optional dependencies (e.g. loaded inside `try/except ImportError`):
- Add to `[project.optional-dependencies]` in `pyproject.toml`
- Add to `optional=(...)` in the manifest

---

## 5. Running the Architecture Checker

From the repository root:

```bash
python tools/arch_check.py
```

Exits with code `0` if no violations are found, `1` otherwise.

To generate a JSON report of all violations:

```bash
python tools/arch_check.py --fix-report violations.json
```

The checker can also be imported as a library:

```python
from tools.arch_check import run_checks
from pathlib import Path

violations = run_checks(Path("."))
```

---

## 6. Error Codes

| Code | Description |
|------|-------------|
| **ARCH001** | Import of a sibling namespace that is not declared in `pyproject.toml` dependencies and `module.py` requires/optional. |
| **ARCH002** | Import of a private module (name segment starting with `_`) from a sibling package. Use the public API instead. |
| **ARCH003** | Import of a concrete `*Provider` class from a sibling module when a capability contract exists. Use the contract interface, not the implementation. |
| **ARCH004** | A `cauldron-*` package appears in `pyproject.toml` dependencies but the corresponding module slug is missing from `module.py` requires/optional (or vice versa). |
