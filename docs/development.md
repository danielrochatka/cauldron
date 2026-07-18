# Development

## Python package

Install the Cauldron Python package in editable mode with development dependencies:

```bash
python -m pip install -e '.[dev]'
```

Run tests:

```bash
pytest
```

Run the Django consumer example check:

```bash
cd examples/django-consumer
PYTHONPATH=../../src python manage.py check
```

## Astro package

Build and type-check the Astro integration package independently:

```bash
cd packages/cauldron-astro
npm install
npm run build
npm run typecheck
```

The Astro consumer example references the package through a local file dependency to demonstrate dependency consumption without copying package source.

## Initial repository structure

```text
src/cauldron/                  Cauldron Python package
packages/cauldron-astro/       Cauldron Astro integration package
examples/django-consumer/      Django dependency consumer fixture
examples/astro-consumer/       Astro dependency consumer fixture
tests/                         Automated Python tests
docs/                          Architecture and development documentation
```
