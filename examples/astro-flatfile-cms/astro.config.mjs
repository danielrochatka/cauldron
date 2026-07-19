import { defineConfig } from 'astro/config';
import { cauldronAstro } from '@procyonsoft/cauldron-astro';

export default defineConfig({
  integrations: [cauldronAstro({ contentRoot: './site/content' })],
});
