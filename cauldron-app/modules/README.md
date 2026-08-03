# Project modules

Place local Cauldron modules here as direct child directories.

Each directory must be a Python package (containing `__init__.py`) that
exposes a `module` attribute satisfying the `CauldronModule` protocol:

```
modules/
  my_module/
    __init__.py   ← exposes `module = BaseModule(ModuleManifest(...))`
```

Modules discovered here take priority over installed-package modules when
slug conflicts occur. The `modules/` root is added to `sys.path` automatically
so each package can be imported by its directory name.

See `CAULDRON_PROJECT_MODULE_ROOT` in `cauldron_site/settings.py`.
