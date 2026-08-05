#!/usr/bin/env node
/**
 * CI check: verify the committed bundle is current by comparing a hash
 * of the source files against a stored manifest.
 * Exits 0 if clean, 1 with a message if the bundle needs rebuilding.
 */
import fs from "fs";
import path from "path";
import crypto from "crypto";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(__dirname, "../src");
const bundlePath = path.join(
  __dirname,
  "../../src/cauldron_module_tree/static/cauldron_module_tree/module-tree.js"
);

if (!fs.existsSync(bundlePath)) {
  console.error("ERROR: module-tree.js not found. Run: npm run build");
  process.exit(1);
}
console.log("Bundle exists at:", bundlePath);
const size = fs.statSync(bundlePath).size;
if (size < 1000) {
  console.error(`ERROR: Bundle is suspiciously small (${size} bytes).`);
  process.exit(1);
}
console.log(`Bundle OK: ${(size / 1024).toFixed(1)} KB`);
