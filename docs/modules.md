# Cauldron Module Specification

Cauldron modules are independently installable Python distributions that extend the platform with new capabilities. Cauldron core discovers, validates, and activates them automatically; it never contains a hardcoded list of modules.

---

## Module structure

A module is a standard Python distribution package that:

1. Declares a `cauldron.modules` entry point pointing to a `CauldronModule` instance.
2. Exposes a `ModuleManifest` describing its identity, version constraints, dependencies, and capabilities.
3. Optionally subclasses `BaseModule` and overrides `on_ready()` for activation-time logic.

### Minimal example layout

```
cauldron-content/
  pyproject.toml
  src/
    cauldron_content/
      __init__.py
```

### `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cauldron-content"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = ["cauldron>=0.1.0"]

[project.entry-points."cauldron.modules"]
"cauldron.content" = "cauldron_content:module"

[tool.hatchling.build.targets.wheel]
packages = ["src/cauldron_content"]
```

The entry-point **name** (left of `=`) is arbitrary; the **value** (right of `=`) must resolve to an object satisfying the `CauldronModule` protocol.

### `src/cauldron_content/__init__.py`

```python
from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.content",
    label="Cauldron Content",
    version="1.0.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_content",),
    requires=(
        ModuleRequirement(slug="cauldron.accounts"),
    ),
    provides=("cauldron.capability.content",),
)

module = BaseModule(_manifest)
```

---

## ModuleManifest fields

| Field | Type | Default | Description |
|---|---|---|---|
| `slug` | `str` | required | Unique dotted identifier, e.g. `cauldron.content`. Lowercase letters and digits only; segments separated by dots. |
| `label` | `str` | required | Human-readable display name. |
| `version` | `str` | `"0.0.0"` | PEP 440 version string for this module. |
| `cauldron_version` | `str` | `""` | PEP 440 specifier constraining the required Cauldron version, e.g. `">=0.1.0,<2.0.0"`. Empty means no constraint. |
| `django_apps` | `tuple[str, ...]` | `()` | Django app labels this module registers. These must also appear in `INSTALLED_APPS` — see *Composing INSTALLED_APPS* below. |
| `django_middleware` | `tuple[str, ...]` | `()` | Middleware class paths this module contributes. Used by `compose_django_settings()` to build `MIDDLEWARE`. |
| `django_context_processors` | `tuple[str, ...]` | `()` | Context processor paths this module contributes. Used by `compose_django_settings()` to build the `context_processors` list in `TEMPLATES`. |
| `settings` | `Mapping[str, object]` | `{}` | Default settings contributed by this module. |
| `requires` | `tuple[ModuleRequirement, ...]` | `()` | Required dependencies. The module is not activated unless all required deps are satisfied. |
| `optional` | `tuple[ModuleRequirement, ...]` | `()` | Optional dependencies. A version mismatch produces a warning; absence is silently ignored. |
| `provides` | `tuple[str, ...]` | `()` | Named capabilities exposed by this module. Other modules may depend on these by capability slug rather than by module slug. |

### ModuleRequirement fields

| Field | Type | Default | Description |
|---|---|---|---|
| `slug` | `str` | required | Module slug or capability slug being depended on. |
| `version` | `str` | `""` | PEP 440 version specifier. Empty means any version is acceptable. |
| `kind` | `"module"` or `"capability"` | `"module"` | Whether `slug` refers to another module or to a named capability. |

Both `ModuleManifest` and `ModuleRequirement` are validated at construction time. Invalid slugs, versions, or specifiers raise `ValueError` immediately.

### Serialization

Both types support `to_dict()` / `from_dict()` for JSON round-trips:

```python
data = manifest.to_dict()       # dict, JSON-serializable
copy = ModuleManifest.from_dict(data)
assert copy == manifest
```

---

## Declaring dependencies

### Required module dependency

```python
ModuleRequirement(slug="cauldron.accounts")                   # any version
ModuleRequirement(slug="cauldron.accounts", version=">=2.0.0")  # minimum version
```

### Required capability dependency

```python
ModuleRequirement(slug="cauldron.capability.auth", kind="capability")
```

Use capability dependencies when you depend on a contract rather than a specific module. Any active module that provides `cauldron.capability.auth` satisfies the requirement.

### Optional dependency

```python
ModuleManifest(
    ...
    optional=(
        ModuleRequirement(slug="cauldron.search", kind="capability"),
    ),
)
```

Optional dependencies affect load ordering when the dependency is present but do not block activation when it is absent.

---

## Capability conflicts

If multiple active modules provide the same capability and another module requires that capability, resolution is ambiguous. The resolver raises `cauldron.E015` and instructs you to set `CAULDRON_CAPABILITY_PROVIDERS`:

```python
# settings.py
CAULDRON_CAPABILITY_PROVIDERS = {
    "cauldron.capability.auth": "cauldron.oauth",  # explicit winner
}
```

---

## Enabling modules (opt-in)

Discovered modules are **not activated unless explicitly enabled**. Activation is controlled by the `CAULDRON_MODULES` Django setting:

```python
# settings.py
CAULDRON_MODULES = {
    "cauldron.content": {},
    "cauldron.accounts": {"allow_signup": False},
}
```

Keys are active module slugs. Values are per-module configuration dicts accessible at runtime via `registry.get_module_config(slug)`. If `CAULDRON_MODULES` is absent, no modules are activated.

---

## Composing INSTALLED_APPS

### Recommended: `compose_django_settings()`

For full settings composition (apps, middleware, and context processors), use `compose_django_settings()` from `cauldron.django`:

```python
from cauldron.django import compose_django_settings

CAULDRON_MODULES = {
    "cauldron.django.state": {},
    "cauldron.django.auth": {},
}

plan = compose_django_settings(
    installed_apps=["django.contrib.contenttypes", "cauldron"],
    middleware=["django.middleware.security.SecurityMiddleware"],
    context_processors=["django.template.context_processors.request"],
    module_settings=CAULDRON_MODULES,
)

INSTALLED_APPS = list(plan.installed_apps)
MIDDLEWARE = list(plan.middleware)
# Use plan.context_processors in TEMPLATES[0]["OPTIONS"]["context_processors"]
```

`compose_django_settings()` discovers installed modules via entry points, resolves dependency order, and collects `django_apps`, `django_middleware`, and `django_context_processors` from each module in topological load order. Base values are prepended; duplicates are removed preserving first occurrence.

### Legacy: `get_module_apps()`

For apps-only composition (backward compatible), use `get_module_apps()`:

```python
from cauldron.modules.discovery import get_module_apps

CAULDRON_MODULES = {
    "cauldron.content": {},
    "cauldron.accounts": {},
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "cauldron",
    *get_module_apps(CAULDRON_MODULES),
]
```

`get_module_apps()` discovers installed modules via entry points and returns their `django_apps` tuples in dependency-resolved order. Modules not in `CAULDRON_MODULES` are ignored.

---

## Lifecycle hooks

Override `on_ready()` in `BaseModule` to run logic after all modules are activated:

```python
class ContentModule(BaseModule):
    def on_ready(self) -> None:
        config = registry.get_module_config(self.slug)
        if config.get("register_signals", True):
            from . import signals  # noqa: F401
```

`on_ready()` is called in dependency order — dependencies run before dependents. It is **not** called if any discovery or resolution errors exist; those must be fixed first.

---

## Per-module configuration

Site configuration for each module lives in the `CAULDRON_MODULES` dict value:

```python
CAULDRON_MODULES = {
    "cauldron.email": {
        "backend": "django.core.mail.backends.smtp.EmailBackend",
        "default_from": "no-reply@example.com",
    },
}
```

Modules read their config via the global registry inside `on_ready()`:

```python
from cauldron.modules.registry import registry

class EmailModule(BaseModule):
    def on_ready(self) -> None:
        config = registry.get_module_config(self.slug)
        backend = config.get("backend", "django.core.mail.backends.console.EmailBackend")
```

---

## Dependency graph output

The registry exposes a machine-readable dependency graph for tooling:

```python
from cauldron.modules.registry import registry

graph = registry.dependency_graph()
# {"cauldron.content": ["cauldron.accounts"], "cauldron.accounts": []}
```

Keys and values are both deterministically sorted slug lists.

---

## Django system checks reference

| ID | Level | Meaning |
|---|---|---|
| `cauldron.I001` | Info | Cauldron core initialized successfully. |
| `cauldron.I002` | Info | Lists active module slugs. |
| `cauldron.E010` | Error | Missing required module dependency. |
| `cauldron.E011` | Error | Missing required capability provider. |
| `cauldron.E012` | Error | Installed module version does not satisfy constraint. |
| `cauldron.E013` | Error | Cauldron version does not satisfy module's constraint. |
| `cauldron.E014` | Error | Circular dependency detected. |
| `cauldron.E015` | Error | Multiple capability providers; explicit resolution required. |
| `cauldron.E020` | Error | Entry-point failed to load. |
| `cauldron.E021` | Error | Duplicate module slug registered by two entry points. |
| `cauldron.E022` | Error | Module manifest failed validation. |
| `cauldron.W010` | Warning | Optional dependency version mismatch. |

Run `python manage.py check` to surface all module graph issues before starting the application.
