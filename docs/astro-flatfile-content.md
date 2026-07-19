# Astro flat-file content loader

`@procyonsoft/cauldron-astro` ships `createCauldronContentLoader`, an Astro 5
content loader that reads the same Markdown + JSON-schema layout served by
`cauldron-cms-flatfile`. This is how consumer sites stay build-time-static
while still sharing content identity with the Django editor.

## Usage

```typescript
// src/content/config.ts
import { defineCollection } from 'astro:content';
import { createCauldronContentLoader } from '@procyonsoft/cauldron-astro';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'site');

export const collections = {
  pages: defineCollection({
    loader: createCauldronContentLoader({ siteRoot, collection: 'pages' }),
  }),
};
```

`siteRoot` should contain a `content/` subdirectory (override via
`contentRoot`) and a `schemas/` subdirectory (override via `schemaRoot`).

## Options

- `siteRoot` — required absolute path.
- `contentRoot` — default `"content"`.
- `schemaRoot` — default `"schemas"`.
- `collection` — the collection name to load.
- `preview` — include drafts. Default `false`; only enable in preview builds.

## Hash parity

`computeContentHash` produces the same digest as
`cauldron_content.hashing.compute_content_hash`. Fixtures under
`fixtures/content-parity/` are the source of truth for parity — the Python and
TypeScript test suites both assert the resulting hash matches
`expected/*.expected.json`.

## Convenience helper

`loadCauldronCollection(options)` returns the items synchronously without an
Astro context. Useful in tests, build steps, and scripts.
