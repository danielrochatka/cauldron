import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { createHash } from 'node:crypto';
import matter from 'gray-matter';
/** Normalize line endings and ensure a single trailing newline for non-empty bodies. */
export function normalizeBody(body) {
    if (!body)
        return '';
    const normalized = body.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (normalized.length === 0)
        return '';
    return normalized.endsWith('\n') ? normalized : normalized + '\n';
}
/** Deep-sort object keys for canonical JSON serialization. */
function sortDeep(obj) {
    if (Array.isArray(obj))
        return obj.map(sortDeep);
    if (obj !== null && typeof obj === 'object') {
        const sorted = {};
        for (const key of Object.keys(obj).sort()) {
            sorted[key] = sortDeep(obj[key]);
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
export function computeContentHash(id, collection, slug, status, schema, data, body) {
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
function assertSafePath(root, target) {
    const resolvedRoot = resolve(root);
    const resolvedTarget = resolve(target);
    if (!resolvedTarget.startsWith(resolvedRoot + sep) &&
        resolvedTarget !== resolvedRoot) {
        throw new Error(`Path escapes root: ${target}`);
    }
}
/**
 * Create an Astro content loader that reads a Cauldron flat-file collection.
 */
export function createCauldronContentLoader(options) {
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
            logger?.info?.(`cauldron-flatfile: loaded ${items.length} item(s) from ${options.collection}`);
        },
    };
}
/**
 * Load all items from a collection directly (without Astro). Useful for tests
 * and for build steps that need the parsed data outside of an Astro context.
 */
export function loadCauldronCollection(options) {
    const siteRoot = resolve(options.siteRoot);
    const contentRoot = join(siteRoot, options.contentRoot ?? 'content');
    const collectionDir = join(contentRoot, options.collection);
    const preview = options.preview ?? false;
    assertSafePath(siteRoot, collectionDir);
    let files;
    try {
        files = readdirSync(collectionDir)
            .filter((f) => f.endsWith('.md'))
            .sort();
    }
    catch {
        return [];
    }
    const seenIds = new Map();
    const seenSlugs = new Map();
    const items = [];
    for (const file of files) {
        const filePath = join(collectionDir, file);
        assertSafePath(siteRoot, filePath);
        const raw = readFileSync(filePath, 'utf8');
        const parsed = matter(raw);
        const meta = parsed.data;
        const id = meta.id;
        const slug = meta.slug;
        const status = meta.status;
        const schema = meta.schema;
        if (!id || !slug || !status || !schema) {
            throw new Error(`Missing required front-matter field in ${filePath} (need id, slug, status, schema)`);
        }
        const data = {};
        for (const [k, v] of Object.entries(meta)) {
            if (!RESERVED_FIELDS.has(k))
                data[k] = v;
        }
        const idStr = String(id);
        const slugStr = String(slug);
        const statusStr = String(status).toLowerCase();
        if (statusStr !== 'published' && statusStr !== 'draft') {
            throw new Error(`Unknown status ${JSON.stringify(status)} in ${filePath}`);
        }
        if (seenIds.has(idStr)) {
            throw new Error(`Duplicate content ID ${JSON.stringify(idStr)} in ${filePath} and ${seenIds.get(idStr)}`);
        }
        if (seenSlugs.has(slugStr)) {
            throw new Error(`Duplicate slug ${JSON.stringify(slugStr)} in ${filePath} and ${seenSlugs.get(slugStr)}`);
        }
        seenIds.set(idStr, filePath);
        seenSlugs.set(slugStr, filePath);
        if (!preview && statusStr === 'draft')
            continue;
        // ``python-frontmatter`` strips the newline separating the closing YAML
        // fence from the body; gray-matter keeps it. Strip a single leading LF to
        // stay byte-parity with the Python parser before normalization.
        let rawBody = parsed.content ?? '';
        if (rawBody.startsWith('\n'))
            rawBody = rawBody.slice(1);
        const bodyNorm = normalizeBody(rawBody);
        const hash = computeContentHash(idStr, options.collection, slugStr, statusStr, String(schema), data, bodyNorm);
        items.push({
            id: idStr,
            collection: options.collection,
            slug: slugStr,
            status: statusStr,
            schema: String(schema),
            data,
            body: bodyNorm,
            hash,
        });
    }
    return items;
}
/** Convenience wrapper for use in astro.config.mjs. */
export function defineCauldronFlatFileSource(options) {
    return options;
}
