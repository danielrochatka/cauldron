import { defineCollection } from 'astro:content';
import { createCauldronContentLoader } from '@procyonsoft/cauldron-astro';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const siteRoot = join(__dirname, '..', '..', 'site');

export const collections = {
  pages: defineCollection({
    loader: createCauldronContentLoader({ siteRoot, collection: 'pages' }),
  }),
  posts: defineCollection({
    loader: createCauldronContentLoader({ siteRoot, collection: 'posts' }),
  }),
};
