import type { AstroIntegration } from 'astro';
export interface CauldronAstroOptions {
    /** Filesystem root where the consuming site keeps its own content. */
    contentRoot?: string;
    /** Name of the site-owned theme selected by the consumer. */
    theme?: string;
}
export interface CauldronContentSource {
    kind: 'site-content';
    root: string;
}
export declare function defineCauldronContentSource(root: string): CauldronContentSource;
export declare function cauldronAstro(options?: CauldronAstroOptions): AstroIntegration;
export type CauldronThemeContract = {
    name: string;
    entrypoint: string;
};
export { computeContentHash, createCauldronContentLoader, defineCauldronFlatFileSource, loadCauldronCollection, normalizeBody, } from './loaders/flatfile.js';
export type { CauldronFlatFileSourceOptions, ContentItem, ContentStatus, FlatFileLoader, FlatFileLogger, FlatFileStore, } from './loaders/flatfile.js';
