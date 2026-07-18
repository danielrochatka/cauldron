# Cauldron

Cauldron is Procyonsoft's private, reusable CMS and AI application platform foundation. Websites and applications install Cauldron as a versioned dependency, update it like a conventional CMS, and extend it through supported modules, themes, configuration, and content.

Cauldron is not copy-and-modify boilerplate. Django and Astro remain upstream dependencies; their source code is not vendored, forked, or embedded here.

## Repository map

- `src/cauldron/` — installable Python package and Django app integration.
- `packages/cauldron-astro/` — separately packaged Astro integration layer.
- `examples/django-consumer/` — Django project consuming the installed Cauldron package.
- `examples/astro-consumer/` — Astro site consuming `@procyonsoft/cauldron-astro`.
- `tests/` — Python tests for package, Django, health, and boundary behavior.
- `docs/` — architecture and development notes.

## Packages

- Python distribution/import name: `cauldron` version `0.1.0`.
- Astro package: `@procyonsoft/cauldron-astro` version `0.1.0`.

## Quick start

```bash
python -m pip install -e '.[dev]'
pytest
cd packages/cauldron-astro
npm install
npm run build
npm run typecheck
```

See `docs/architecture.md` and `docs/development.md` for boundaries, commands, and deferred scope.
