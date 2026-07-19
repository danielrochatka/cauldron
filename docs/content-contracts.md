# Content contracts

The `cauldron-content` package defines the value types and protocols that every
content backend implements. Backends live in separate packages (e.g.
`cauldron-cms-flatfile`) and register themselves with the shared repository
registry so callers never need to know which provider is serving a request.

## Value types

- `ContentItem` — a single content record with an `id`, `collection`, `slug`,
  `status` (`draft` | `published`), `schema` name, arbitrary `data`, normalized
  `body`, and a canonical `hash`. All mutable fields are defensively copied.
- `ContentChangeSet` / `ContentOperation` — describe the CRUD intents a caller
  sends to a repository. Operations carry an optional `expected_hash` for
  optimistic concurrency.
- `ApplyResult` — reports whether a change-set was applied, plus any
  `Conflict`s or `ValidationIssue`s.
- `RepositoryHealth`, `RepositoryDescriptor` — self-description helpers used by
  admin surfaces and system checks.

## The `ContentRepository` protocol

`ContentRepository` is a runtime-checkable `Protocol` so any object that
provides the expected methods can be registered. Repositories must expose:

- `describe()`, `health()`
- `list_collections()`, `list_items(collection, include_drafts=False)`
- `get_by_id(item_id, include_drafts=False)`, `get_by_slug(...)`
- `validate(item)`, `apply(changeset)`

Drafts are excluded from every read path unless the caller explicitly opts in
via `include_drafts=True`.

## Canonical content hash

`cauldron_content.hashing.compute_content_hash` returns a lowercase SHA-256 of
a canonical serialization of the item (see `docs/flatfile-cms.md` for the
byte-by-byte algorithm). The TypeScript loader in `@procyonsoft/cauldron-astro`
produces the same digest for the same inputs, which is how the Astro build and
Django admin agree on identity.
