import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { createHash } from 'node:crypto';
import matter from 'gray-matter';

export type ContentStatus = 'draft' | 'published';

export interface ContentItem {
  id: string;
  collection: string;
  slug: string;
  status: ContentStatus;
  schema: string;
  data: Record<string, unknown>;
  body: string;
  hash: string;
}

export interface CauldronFlatFileSourceOptions {
  /** Absolute path to the site root (contains content/ and schemas/ dirs). */
  siteRoot: string;
  /** Directory name relative to siteRoot for content (default: "content"). */
  contentRoot?: string;
  /** Directory name relative to siteRoot for schemas (default: "schemas"). */
  schemaRoot?: string;
  /** Collection name to load. */
  collection: string;
  /** Include draft content (default: false). */
  preview?: boolean;
}

/** Normalize line endings and ensure a single trailing newline for non-empty bodies. */
export function normalizeBody(body: string): string {
  if (!body) return '';
  const normalized = body.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (normalized.length === 0) return '';
  return normalized.endsWith('\n') ? normalized : normalized + '\n';
}

/** Deep-sort object keys for canonical JSON serialization. */
function sortDeep(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(sortDeep);
  if (obj !== null && typeof obj === 'object') {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj as object).sort()) {
      sorted[key] = sortDeep((obj as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return obj;
}

/**
 * Compute the canonical SHA-256 content hash.
 *
 * MUST match ``cauldron_content.hashing.compute_content_hash`` byte-for-byte.
 * The canonical object below already has keys in alphabetical order so the
 * default JSON.stringify emits the same bytes as Python's
 * ``json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=False)``.
 */
export function computeContentHash(
  id: string,
  collection: string,
  slug: string,
  status: string,
  schema: string,
  data: Record<string, unknown>,
  body: string,
): string {
  const canonical = {
    body: normalizeBody(body),
    collection,
    data: sortDeep(data),
    id,
    schema,
    slug,
    status,
  };
  const serialized = JSON.stringify(canonical);
  return createHash('sha256').update(serialized, 'utf8').digest('hex');
}

const RESERVED_FIELDS = new Set(['id', 'slug', 'status', 'schema']);

function assertSafePath(root: string, target: string): void {
  const resolvedRoot = resolve(root);
  const resolvedTarget = resolve(target);
  if (
    !resolvedTarget.startsWith(resolvedRoot + sep) &&
    resolvedTarget !== resolvedRoot
  ) {
    throw new Error(`Path escapes root: ${target}`);
  }
}

/**
 * Minimal store shape expected from the Astro loader context.
 * We deliberately depend on the surface we use (set/clear) rather than
 * pulling in Astro's public types so this loader stays testable in isolation.
 */
export interface FlatFileStore {
  set(entry: { id: string; data: Record<string, unknown>; body?: string }): boolean | void;
  clear(): void;
}

export interface FlatFileLogger {
  info?(msg: string): void;
  warn?(msg: string): void;
}

export interface FlatFileLoader {
  name: string;
  load(context: {
    store: FlatFileStore;
    logger?: FlatFileLogger;
    collection?: string;
  }): Promise<void>;
}

/**
 * Create an Astro content loader that reads a Cauldron flat-file collection.
 */
export function createCauldronContentLoader(
  options: CauldronFlatFileSourceOptions,
): FlatFileLoader {
  return {
    name: 'cauldron-flatfile',
    async load({ store, logger }) {
      const items = loadCauldronCollection(options);
      store.clear();
      for (const item of items) {
        store.set({
          id: item.id,
          data: {
            ...item.data,
            _cauldron: {
              id: item.id,
              collection: item.collection,
              slug: item.slug,
              status: item.status,
              schema: item.schema,
              hash: item.hash,
            },
          },
          body: item.body,
        });
      }
      logger?.info?.(
        `cauldron-flatfile: loaded ${items.length} item(s) from ${options.collection}`,
      );
    },
  };
}

/**
 * Load all items from a collection directly (without Astro). Useful for tests
 * and for build steps that need the parsed data outside of an Astro context.
 */
export function loadCauldronCollection(
  options: CauldronFlatFileSourceOptions,
): ContentItem[] {
  const siteRoot = resolve(options.siteRoot);
  const contentRoot = join(siteRoot, options.contentRoot ?? 'content');
  const collectionDir = join(contentRoot, options.collection);
  const preview = options.preview ?? false;

  assertSafePath(siteRoot, collectionDir);

  let files: string[];
  try {
    files = readdirSync(collectionDir)
      .filter((f) => f.endsWith('.md'))
      .sort();
  } catch {
    return [];
  }

  const seenIds = new Map<string, string>();
  const seenSlugs = new Map<string, string>();
  const items: ContentItem[] = [];

  for (const file of files) {
    const filePath = join(collectionDir, file);
    assertSafePath(siteRoot, filePath);

    const raw = readFileSync(filePath, 'utf8');
    const parsed = matter(raw);
    const meta = parsed.data as Record<string, unknown>;

    const id = meta.id;
    const slug = meta.slug;
    const status = meta.status;
    const schema = meta.schema;
    if (!id || !slug || !status || !schema) {
      throw new Error(
        `Missing required front-matter field in ${filePath} (need id, slug, status, schema)`,
      );
    }

    const data: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(meta)) {
      if (!RESERVED_FIELDS.has(k)) data[k] = v;
    }

    const idStr = String(id);
    const slugStr = String(slug);
    const statusStr = String(status).toLowerCase();
    if (statusStr !== 'published' && statusStr !== 'draft') {
      throw new Error(`Unknown status ${JSON.stringify(status)} in ${filePath}`);
    }

    if (seenIds.has(idStr)) {
      throw new Error(
        `Duplicate content ID ${JSON.stringify(idStr)} in ${filePath} and ${seenIds.get(idStr)}`,
      );
    }
    if (seenSlugs.has(slugStr)) {
      throw new Error(
        `Duplicate slug ${JSON.stringify(slugStr)} in ${filePath} and ${seenSlugs.get(slugStr)}`,
      );
    }
    seenIds.set(idStr, filePath);
    seenSlugs.set(slugStr, filePath);

    if (!preview && statusStr === 'draft') continue;

    // ``python-frontmatter`` strips the newline separating the closing YAML
    // fence from the body; gray-matter keeps it. Strip a single leading LF to
    // stay byte-parity with the Python parser before normalization.
    let rawBody = parsed.content ?? '';
    if (rawBody.startsWith('\n')) rawBody = rawBody.slice(1);
    const bodyNorm = normalizeBody(rawBody);
    const hash = computeContentHash(
      idStr,
      options.collection,
      slugStr,
      statusStr,
      String(schema),
      data,
      bodyNorm,
    );

    items.push({
      id: idStr,
      collection: options.collection,
      slug: slugStr,
      status: statusStr as ContentStatus,
      schema: String(schema),
      data,
      body: bodyNorm,
      hash,
    });
  }

  return items;
}

/** Convenience wrapper for use in astro.config.mjs. */
export function defineCauldronFlatFileSource(
  options: CauldronFlatFileSourceOptions,
): CauldronFlatFileSourceOptions {
  return options;
}
