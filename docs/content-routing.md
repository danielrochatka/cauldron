# Content routing

`cauldron-content` ships a `RepositoryRegistry` singleton and a `ContentRouter`
that together let a site mix multiple providers (flat-file, SQL, remote) and
route each collection to the correct one.

## Registration

Repositories register themselves under a provider name at process start-up:

```python
from cauldron_content import registry
from cauldron_cms_flatfile.repository import FlatFileRepository, PROVIDER_NAME
from cauldron_cms_flatfile.config import FlatFileCMSConfig

registry.register(PROVIDER_NAME, FlatFileRepository(FlatFileCMSConfig(site_root=...)))
```

Registering the same name twice raises `RegistrationError`; use
`registry.reset()` in tests for isolation.

## Routing

`ContentRouter` takes a `RouterConfig` with:

- `default_provider` — used when no per-collection mapping is set.
- `collections` — a `{collection_name: provider_name}` mapping that overrides
  the default.

```python
from cauldron_content import ContentRouter, RouterConfig, registry

router = ContentRouter(
    registry,
    RouterConfig(default_provider="flatfile", collections={"blog": "wagtail"}),
)
```

The router mirrors the `ContentRepository` API (`list_items`, `get_by_id`,
`get_by_slug`, `apply`). Missing providers raise `RouterError` at call time so
misconfiguration surfaces during requests rather than at import time.

## Django system checks

Setting `CAULDRON_MODULES["cauldron.content"]["routing"]` runs the
`cauldron.content.I400` info check. Type errors in the routing dict emit
`cauldron.content.E400`–`E402`.
