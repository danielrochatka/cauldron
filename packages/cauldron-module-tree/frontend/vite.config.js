import { defineConfig } from "vite";

export default defineConfig({
  test: {
    environment: "jsdom",
  },
  build: {
    lib: {
      entry: "src/main.js",
      name: "ModuleTree",
      fileName: "module-tree",
      formats: ["iife"],
    },
    outDir: "../src/cauldron_module_tree/static/cauldron_module_tree",
    emptyOutDir: false,
    rollupOptions: {
      output: {
        // Single bundle — no code splitting for a simple Django static asset
        inlineDynamicImports: true,
        // Override the default iife naming (module-tree.iife.js → module-tree.js)
        entryFileNames: "module-tree.js",
      },
    },
    minify: true,
    // ELK uses a web worker; bundle it inline using the ?worker&inline pattern
    target: "es2020",
  },
});
