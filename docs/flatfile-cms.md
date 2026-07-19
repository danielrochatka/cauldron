# Flat-file CMS

`cauldron-cms-flatfile` implements the `ContentRepository` protocol on top of
a directory of Markdown files with YAML front matter. It is the reference
provider and the fastest way to bring a Cauldron site online.

## Layout

```
site/
  content/
    <collection>/
      <slug>.md
  schemas/
    <schema>.schema.json
```

Each Markdown file must set four reserved front-matter fields:

- `id` — stable, globally unique identifier (e.g. `page.home`).
- `slug` — URL slug (unique per collection).
- `status` — `published` or `draft`.
- `schema` — name of the JSON Schema used to validate this item.

All other front-matter keys become `ContentItem.data` and are validated against
the referenced schema on write.

## Canonical content hash

`cauldron_content.hashing.compute_content_hash` builds the canonical form:

```json
{
  "body": "<LF-normalized body with a single trailing newline>",
  "collection": "...",
  "data": <deep-sorted front-matter minus reserved fields>,
  "id": "...",
  "schema": "...",
  "slug": "...",
  "status": "..."
}
```

The bytes are serialized with `json.dumps(..., sort_keys=True,
separators=(',', ':'), ensure_ascii=False)` and hashed with SHA-256. The
TypeScript loader in `@procyonsoft/cauldron-astro` produces the same digest —
this is verified by the shared fixtures under `fixtures/content-parity/`.

## Reading content

`FlatFileRepository` supports:

- `list_collections()`, `list_items(collection, include_drafts=False)`
- `get_by_id(item_id, include_drafts=False)`, `get_by_slug(...)`
- `validate(item)` — loads `schemas/<schema>.schema.json` and runs
  `jsonschema.Draft202012Validator`
- `apply(changeset)` — atomic CREATE/UPDATE/DELETE with optimistic concurrency.

Drafts are excluded by default from every read path.

## Writing content

`apply()` stages every operation (validating, checking hashes, and rendering
the resulting Markdown file), and only touches the filesystem when the entire
change-set is safe to apply. Writes use `os.replace` with temp files; if a
mid-flight failure occurs the partially-written files are restored from
in-memory backups.

## Management commands

- `python manage.py cauldron_content_validate [--json] [--include-drafts]`
  validates every published item and exits non-zero if any schema errors are
  found.
- `python manage.py cauldron_content_list [--collection <name>] [--json]
  [--include-drafts]` prints a table (or JSON) of every item.

## System checks

- `cauldron.cms.flatfile.I600` — configuration looks healthy (info).
- `cauldron.cms.flatfile.E600` — `site_root` is not absolute.
- `cauldron.cms.flatfile.E601` — `site_root` does not exist.
- `cauldron.cms.flatfile.E602` — `content_root` is invalid.
