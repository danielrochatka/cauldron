# Project modules

Place local Cauldron modules here as direct child directories.

Each directory must be a Python package (containing `__init__.py`) that
exposes a `module` attribute satisfying the `CauldronModule` protocol.

## Layout

```
modules/
  my_feature/
    __init__.py        ← exposes `module = BaseModule(ModuleManifest(...))`
    apps.py            ← optional: Django AppConfig
    models.py          ← optional: Django models
```

## Minimal example

```python
# modules/my_feature/__init__.py
from cauldron.modules import BaseModule, ModuleManifest

module = BaseModule(ModuleManifest(
    slug="my.feature",
    label="My Feature",
    version="0.1.0",
    django_apps=("my_feature",),
))
```

Enable it in `cauldron_site/settings.py`:

```python
CAULDRON_MODULES = {
    ...
    "my.feature": {},
}
```

## Path and import rules

- Only **direct children** of `CAULDRON_PROJECT_MODULE_ROOT` are scanned.
- Directory names must be valid Python identifiers.
- Hidden (`.`-prefixed), dunder (`__...__`), `build/`, `dist/`, and `.egg-info`
  directories are silently ignored.
- Symlinks whose resolved target lies outside the project root are rejected.
- Project modules take **slug priority** over installed-package entry points.

## Converting to a packaged module

To convert a project module to an installable package, add a `pyproject.toml`
and register the entry point:

```toml
[project.entry-points."cauldron.modules"]
"my.feature" = "my_feature:module"
```

Once installed, remove the directory from `modules/` to avoid slug conflicts.

See `CAULDRON_PROJECT_MODULE_ROOT` in `cauldron_site/settings.py`.
