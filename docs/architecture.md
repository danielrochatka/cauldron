# Cauldron architecture foundation

## Dependency model

A website or application depends on Cauldron. Cauldron depends on Django for the administrative application platform and provides a separate Astro integration package for public rendering workflows.

Django and Astro are upstream frameworks. Cauldron declares them as dependencies or peer dependencies and does not copy, vendor, fork, or embed their source code.

## Ownership boundaries

Cauldron-owned code lives in `src/cauldron/` and `packages/cauldron-astro/`. Site-owned modules, themes, content, media, and configuration live in consuming projects such as `examples/django-consumer/` and `examples/astro-consumer/`.

The canonical future source of truth for ordinary CMS content is filesystem content managed by the consuming site and Git. SQL, search indexes, vector indexes, and caches are future derived or operational capabilities, not part of this milestone.

## Python extension contract

`src/cauldron/modules/__init__.py` defines a small `CauldronModule` protocol and `ModuleManifest` dataclass. These document how future Cauldron-owned and site-owned modules can declare Django apps and settings without hardcoding one website into core.

## Astro extension contract

`@procyonsoft/cauldron-astro` exports `cauldronAstro`, `defineCauldronContentSource`, and theme/content TypeScript contracts. Placeholder folders reserve future content loader, schema, rendering helper, and build hook surfaces.

## Content stack

The content layer is split across three cooperating modules:

- `cauldron.content` — value types (`ContentItem`, `ContentChangeSet`), the
  canonical hash algorithm, the `RepositoryRegistry`, and the `ContentRouter`.
- `cauldron.cms.flatfile` — the reference provider, reading Markdown + YAML
  front matter validated against JSON Schema.
- `cauldron.workspace.flatfile` — optional editor scratch space with
  change-sets, snapshots, and file locks.

The Astro loader in `@procyonsoft/cauldron-astro` reads the same on-disk
layout and computes byte-identical content hashes, so build-time and edit-time
agree on identity. See `docs/content-contracts.md`, `docs/flatfile-cms.md`,
`docs/flatfile-workspace.md`, and `docs/astro-flatfile-content.md`.

## Content control plane

The content control plane adds a permissioned mutation layer on top of the content stack:

- `ContentOperationService` is the **single application service** for all content mutations.
  API layers, the Django Admin, and future AI agents all delegate to this service.
  Authorization is enforced here — callers must not re-implement permission checks.
- A lifecycle state machine (`PROPOSED → VALIDATED → APPROVED → APPLYING → APPLIED`)
  provides auditability, reversibility, and separation of duties.
- The `ContentPermissionProxy` model hosts nine permission codenames that map to each
  lifecycle action. Groups can be configured to enforce four-eyes approval.
- An append-only `ContentAuditEvent` table records every state transition with actor,
  timestamps, and correlation IDs.
- Reconciliation (`cauldron_content_reconcile`) detects and finalizes requests that were
  interrupted during application.

The HTTP API (`cauldron.content.api`) and Django Admin integration (`cauldron.admin.content`)
are thin layers over `ContentOperationService`. A future AI Admin module will follow the same
pattern.

## Deferred modules

SQL/Wagtail providers, media library, search, RAG/vector, imports, forms,
deployment, billing, tenancy, and production operations are intentionally deferred.
