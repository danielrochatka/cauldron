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
export declare function normalizeBody(body: string): string;
/**
 * Compute the canonical SHA-256 content hash.
 *
 * MUST match ``cauldron_content.hashing.compute_content_hash`` byte-for-byte.
 * The canonical object below already has keys in alphabetical order so the
 * default JSON.stringify emits the same bytes as Python's
 * ``json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=False)``.
 */
export declare function computeContentHash(id: string, collection: string, slug: string, status: string, schema: string, data: Record<string, unknown>, body: string): string;
/**
 * Minimal store shape expected from the Astro loader context.
 * We deliberately depend on the surface we use (set/clear) rather than
 * pulling in Astro's public types so this loader stays testable in isolation.
 */
export interface FlatFileStore {
    set(entry: {
        id: string;
        data: Record<string, unknown>;
        body?: string;
    }): boolean | void;
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
export declare function createCauldronContentLoader(options: CauldronFlatFileSourceOptions): FlatFileLoader;
/**
 * Load all items from a collection directly (without Astro). Useful for tests
 * and for build steps that need the parsed data outside of an Astro context.
 */
export declare function loadCauldronCollection(options: CauldronFlatFileSourceOptions): ContentItem[];
/** Convenience wrapper for use in astro.config.mjs. */
export declare function defineCauldronFlatFileSource(options: CauldronFlatFileSourceOptions): CauldronFlatFileSourceOptions;
