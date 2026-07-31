# Cauldron — Claude Code Instructions

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the module boundary rules,
public API contract, capability contract guidelines, and how to declare
cross-package dependencies.

### Key rule

Every cross-boundary import must be declared in **both** `pyproject.toml`
and the package's `module.py` manifest. Run the architecture checker to
verify:

```bash
python tools/arch_check.py
```
