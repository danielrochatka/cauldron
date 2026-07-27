import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  outDir: process.env.CAULDRON_OUTDIR || 'dist',
  build: {
    format: 'directory',
  },
});
