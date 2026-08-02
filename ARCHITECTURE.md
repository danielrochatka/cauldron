# Cauldron Architecture

This document describes the architectural boundaries and conventions enforced
across the Cauldron monorepo.

---

## 1. Module Boundaries

Each package in `packages/cauldron-*/` owns one or more Python namespaces,
declared in its `ModuleManifest.namespaces` field. A package may only import
from a sibling namespace if:

1. The sibling package is listed in `[project.dependencies]` (main) or a
   runtime `[project.optional-dependencies]` group in its `pyproject.toml`, **and**
2. The corresponding `ModuleRequirement(kind='module')` is declared in the manifest
   `requires` or `optional` field with the correct level (see §4).

Both declarations must be kept in sync and at the correct level. The architecture
checker (see §5) enforces this at CI time.

**Platform namespaces** (`cauldron` core, `django`) are always allowed and
do not need explicit declarations.

### Dotted namespace ownership

A namespace may be dotted (e.g. `myapp.core`). Ownership rules:

- A module owning `myapp.core` also owns `myapp.core.api` and every deeper
  sub-path (`myapp.core.startswith("myapp.core.")`).
- It does **not** own `myapp.core_extra` (different root segment after stripping the dot).
- **Sibling dotted namespaces** — `myapp.core` and `myapp.extensions` may be owned
  by different modules without conflict.
- **Parent/child ownership conflict** — one module owning `myapp` and another owning
  `myapp.core` is ambiguous and produces a configuration error. All sub-paths of
  `myapp` belong to the `myapp` owner unless no deeper owner exists.

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

### Dependency categories

| Category | pyproject location | manifest location | Who may import |
|----------|-------------------|-------------------|----------------|
| **Required** | `[project.dependencies]` | `requires=(ModuleRequirement(..., kind='module'),)` | Any file |
| **Runtime-optional** | `[project.optional-dependencies].<group>` (non-`test`) | `optional=(ModuleRequirement(..., kind='module'),)` | Any file (feature must be absent-safe) |
| **Test-only** | `[project.optional-dependencies].test` | *(no manifest entry)* | Test files only |

The level must match in both files:
- A package in `[project.dependencies]` **must** have its slug under manifest `requires=`.
- A package in a runtime optional group **must** have its slug under manifest `optional=`.
- A package in the `test` optional group needs **no manifest entry** — it is not part of the deployed module contract.

Mismatches (e.g. main dep declared as `optional=` in the manifest, or a runtime-optional dep declared as `requires=`) are reported as **ARCH004**.

### Adding a required dependency

Both files must be updated together:

1. **`pyproject.toml`** — add to `[project.dependencies]`:
   ```toml
   dependencies = [
       "cauldron-content>=0.1.0",
   ]
   ```

2. **`module.py`** — add to manifest `requires=`:
   ```python
   requires=(
       ModuleRequirement(slug="cauldron.content", kind="module"),
   ),
   ```

### Adding a runtime-optional dependency

For features that are activated only when the package is present (e.g. loaded
inside `try/except ImportError`):

```toml
[project.optional-dependencies]
flatfile = ["cauldron-cms-flatfile>=0.1.0"]
```

```python
optional=(
    ModuleRequirement(slug="cauldron.cms.flatfile", kind="module"),
),
```

### Adding a test-only dependency

For packages needed only in tests (not shipped with the module):

```toml
[project.optional-dependencies]
test = ["cauldron-site-astro>=0.1.0"]
```

No manifest entry is needed. Test files may import from the package without
a `ModuleRequirement` declaration. Only the `test` group is automatically
treated as test-only; other optional groups are runtime-optional.

### Public API is for supported consumers only

`public_api` entries authorize cross-module imports. Add a path to `public_api`
only when there is a real, supported runtime consumer in another module. Do not
widen `public_api` to accommodate misplaced tests. Move the test to the correct
package instead.

---

## 5. Running the Architecture Checker

From the repository root:

```bash
python tools/arch_check.py
```

Exits with code `0` if no violations are found, `1` otherwise.

To include project-owned module directories (additional module roots beyond `packages/`):

```bash
python tools/arch_check.py --module-root path/to/modules
```

Multiple roots may be supplied; each is scanned like `packages/`.

To generate a JSON report of all violations:

```bash
python tools/arch_check.py --fix-report violations.json
```

---

## 8. Test Boundaries

### Package tests

Test files in `packages/cauldron-*/tests/` follow the same boundaries as production
code with one extension: importing a package listed **only** in
`[project.optional-dependencies].test` (not in main deps or any runtime optional group)
is allowed without a `ModuleRequirement` manifest entry — test extras are not part of
the deployed module contract.

The following rules still apply in test files:

- Private names (`_` prefix) are rejected (ARCH002b).
- Non-public paths absent from the sibling's `public_api` are rejected (ARCH002c).
- Concrete capability implementations are rejected (ARCH003).
- Imports from packages in main `[project.dependencies]` still require `kind='module'`
  under manifest `requires=`, not `optional=` (ARCH001 / ARCH004).
- Imports from runtime-optional packages still require `kind='module'` under manifest
  `optional=` (ARCH001 / ARCH004).

### Provider-specific test ownership

When a test exercises a concrete provider implementation (e.g. tool handlers, models,
or services from a specific provider package), the test belongs in that provider's own
test suite — not in the consumer's. Moving the test:

- Eliminates false cross-package dependencies.
- Avoids widening the provider's `public_api` for test-only callers.
- Keeps private implementation details (`_handle_*`, internal models) private.

### Framework integration tests

Test files in the root `tests/` directory are framework integration tests — they verify
the module system, registry, and discovery across all packages. These tests may import
from any discovered Cauldron package without pyproject declarations, because the monorepo
checkout is the declaration. ARCH002 (private names) and ARCH003 (capability implementations)
still apply.

Fixture packages used exclusively by integration tests (e.g. `cauldron_fixture_alpha`)
belong in `tests/fixtures/`, not in `packages/`, and are **not** listed in the root
`pyproject.toml`. Adding fixture names to published metadata solely to silence the
architecture checker is explicitly prohibited.

Use local fakes, correctly owned tests, or `--module-root` rather than widening
production APIs or adding synthetic pyproject entries.

---

## 6. ModuleManifest Contract

`ModuleManifest` (in `src/cauldron/modules/__init__.py`) is the single source
of truth for what a Cauldron module declares about itself. It is a frozen
dataclass — all fields are validated at construction time and the object is
immutable thereafter.

### What belongs in ModuleManifest

| Field | Type | Purpose |
|-------|------|---------|
| `settings_declarations` | `tuple[ModuleSettingsDeclaration, ...]` | Settings keys this module reads. By default each key lives at `CAULDRON_MODULES[slug][key]`; set `setting_path` to a top-level Django setting name (e.g. `"CAULDRON_UI_OVERRIDES_DIR"`) when the module reads from the global settings namespace instead. Consumed by the settings page (#33). |
| `migration_apps` | `tuple[ModuleMigrationDeclaration, ...]` | Django `app_label`s that hold database migrations. Consumed by startup projection (#38) to determine whether `migrate` is needed. |
| `permissions` | `tuple[ModulePermissionDeclaration, ...]` | Permission `(app_label, codename)` pairs the module introduces. Consumed by install/enable flows (#38) to create permissions before first use. |
| `navigation` | `tuple[ModuleNavigationDeclaration, ...]` | Nav sections and items this module contributes to the admin shell. Consumed by the admin shell (#66) to build the sidebar. |
| `ai_tools` | `tuple[str, ...]` | Tool names this module registers in the AI tool-calling pipeline. Consumed by AI orchestration to validate tool availability. |
| `prompt_templates` | `tuple[str, ...]` | Prompt template names this module provides. Consumed by AI orchestration (#66). |
| `provided_capabilities` | `tuple[ProvidedCapability, ...]` | Rich metadata for each capability slug in `provides`. Each slug must already appear in `provides`. |
| `restart_required` | `bool` | **Stored field.** Explicitly declare that enabling or disabling this module requires a server restart for reasons beyond the Django integration tuples (e.g. the module starts background threads or registers signal handlers in `AppConfig.ready()`). Defaults to `False`. |
| `runtime_requirements` | `tuple[RuntimeRequirement, ...]` | External runtime dependencies (database aliases, cache backends, task workers) needed at startup. Consumed by operational tooling for pre-flight checks. |

**`requires_restart`** is a **derived property** (not serialized). It returns `True`
when any of the following hold:

- `django_apps` is non-empty, **or**
- `django_middleware` is non-empty, **or**
- `django_context_processors` is non-empty, **or**
- `restart_required` is `True`

Use `restart_required=True` only when the module's startup-time side effects are
not expressed by the Django integration tuples.

### What does NOT belong in ModuleManifest

- **Django model schema** — `AppConfig`, `Meta`, field definitions. Django owns
  the database schema; the manifest only declares which `app_label`s contain
  migrations.
- **Permission objects** — `ModulePermissionDeclaration` names a codename; the
  actual `Permission` row is created by Django's `post_migrate` signal. Don't
  duplicate `Meta.permissions`.
- **Route tables / URL patterns** — modules register URLs via `include()` at
  startup, not in the manifest.
- **Signal handlers, middleware configuration** — runtime wiring, not metadata.
- **Anything derivable from code** — if you can read it from the existing
  `AppConfig` or `pyproject.toml`, do not duplicate it in the manifest.

### Validation rules

- **Settings keys** — flat lowercase identifiers (`^[a-z][a-z0-9_]*$`). No
  dots. Two declarations may not share the same key within a manifest.
  If `setting_path` is set it must be an `UPPER_SNAKE_CASE` Django setting name.
- **Migration app_labels** — must correspond to an entry in `django_apps` (exact
  match or last dotted segment, e.g. `"auth"` for `"django.contrib.auth"`). No
  duplicates.
- **Permissions** — codenames are lowercase identifiers (`^[a-z][a-z0-9_]*$`).
  Uniqueness is enforced on the `(app_label, codename)` pair: the same codename
  in two different declared apps is valid. Each `app_label` must correspond to
  an entry in `django_apps`.
- **Navigation keys** — dotted lowercase, hyphens allowed after the first
  character of each segment (e.g. `cauldron.admin.content.page-create`). No
  duplicates. Items (non-empty `section`) must declare a `url_name`. Sections
  (empty `section`) must not set item-only fields (`url_name`, `permission`,
  `url_prefix`, `url_prefix_exact`). Referenced sections need not exist in the
  same manifest — they may be contributed by another module.
- **AI tool / prompt template names** — dotted lowercase, underscores allowed
  in segments (e.g. `content.list_collections`). No duplicates within each
  tuple.
- **ProvidedCapability slugs** — must appear in `provides`. No duplicate slugs
  within `provided_capabilities`. If `contract` is set and the contract path
  is owned by a namespace declared in this manifest, it must fall under one of
  the manifest's `public_api` entries (boundary-aware: `"myapp_extra"` is not
  treated as owned by namespace `"myapp"`).

### Serialization

Every value object and `ModuleManifest` itself supports `to_dict()` and
`from_dict()`. All fields have backward-compatible defaults so that old
serialized dicts (without the new fields) deserialize cleanly.

---

## 7. Error Codes

| Code | Description |
|------|-------------|
| **ARCH001** | Import of a sibling namespace that is not declared in `pyproject.toml` dependencies and `module.py` requires/optional. |
| **ARCH002** | Import of a private module (name segment starting with `_`) from a sibling package. Use the public API instead. |
| **ARCH003** | Import of a concrete `*Provider` class from a sibling module when a capability contract exists. Use the contract interface, not the implementation. |
| **ARCH004** | A `cauldron-*` package appears in `pyproject.toml` dependencies but the corresponding module slug is missing from `module.py` requires/optional (or vice versa). |
