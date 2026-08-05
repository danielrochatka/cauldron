#!/usr/bin/env node
/**
 * CI sanity check: verify the bundle file exists and is not empty.
 * Bundle freshness (committed == rebuilt) is checked by the CI job via
 * `git diff --exit-code` after running `npm run build`.
 * Exits 0 if the bundle passes the sanity check, 1 otherwise.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
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
