/**
 * Cauldron manifest API — Django -> Astro contract.
 *
 * This module is the SINGLE point of contact between the Django build
 * pipeline (SiteBuildService) and the Astro frontend. Every page must
 * import from here — never touch ``process.env.CAULDRON_MANIFEST`` or
 * ``JSON.parse`` on the manifest path directly. When the Python side
 * bumps ``MANIFEST_API_VERSION``, mirror that bump here and update the
 * consumer sites.
 */
import { readFileSync } from 'fs';

export const MANIFEST_API_VERSION = '1.0';

export interface ManifestPage {
  id: string;
  route: string;
  title: string;
  navigation_title: string;
  summary: string;
  body: string;
  template: string;
  seo_title: string;
  meta_description: string;
  canonical_url: string;
  robots_index: boolean;
  robots_follow: boolean;
  social_title: string;
  social_description: string;
  social_image: string;
  nav_visible?: boolean;
}

export interface ManifestTheme {
  css_content: string;
}

export interface CauldronManifest {
  api_version: string;
  pages: ManifestPage[];
  theme: ManifestTheme;
}

/**
 * Read the Cauldron manifest file identified by the CAULDRON_MANIFEST
 * environment variable and return it typed. Throws if the env var is
 * missing (build should fail loudly rather than produce an empty site).
 */
export function loadManifest(): CauldronManifest {
  const manifestPath = process.env.CAULDRON_MANIFEST;
  if (!manifestPath) {
    throw new Error('CAULDRON_MANIFEST environment variable is required.');
  }
  const raw = JSON.parse(readFileSync(manifestPath, 'utf8')) as Partial<CauldronManifest>;
  return {
    api_version: raw.api_version ?? MANIFEST_API_VERSION,
    pages: raw.pages ?? [],
    theme: raw.theme ?? { css_content: '' },
  };
}
