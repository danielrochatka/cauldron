import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { mkdtempSync } from 'node:fs';
import {
  computeContentHash,
  loadCauldronCollection,
  normalizeBody,
  createCauldronContentLoader,
} from '../src/loaders/flatfile.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Walk upward from the test file until we find fixtures/content-parity. This
// keeps the tests portable across both direct src runs and dist-test builds.
function findFixturesDir(): string {
  let cur = __dirname;
  for (let i = 0; i < 8; i++) {
    const candidate = join(cur, 'fixtures', 'content-parity');
    if (existsSync(candidate)) return candidate;
    const parent = dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  throw new Error(`Could not locate fixtures/content-parity starting from ${__dirname}`);
}
const FIXTURES_DIR = findFixturesDir();

test('normalizeBody replaces CRLF and appends trailing newline', () => {
  assert.equal(normalizeBody('a\r\nb'), 'a\nb\n');
  assert.equal(normalizeBody('a\rb'), 'a\nb\n');
  assert.equal(normalizeBody(''), '');
  assert.equal(normalizeBody('a\n'), 'a\n');
});

// The parity fixtures are laid out with the collections directly under the
// fixture root (no ``content/`` subdirectory), so we pass ``contentRoot: '.'``.
const FIXTURE_OPTS = { siteRoot: FIXTURES_DIR, contentRoot: '.' } as const;

test('loadCauldronCollection excludes drafts by default', () => {
  const items = loadCauldronCollection({ ...FIXTURE_OPTS, collection: 'pages' });
  const ids = items.map((i) => i.id);
  assert.ok(ids.includes('page.home'));
  assert.ok(ids.includes('page.about'));
  assert.ok(!ids.includes('page.draft'), 'draft must be excluded');
});

test('loadCauldronCollection preview mode includes drafts', () => {
  const items = loadCauldronCollection({
    ...FIXTURE_OPTS,
    collection: 'pages',
    preview: true,
  });
  const ids = items.map((i) => i.id);
  assert.ok(ids.includes('page.draft'));
});

test('parity: hashes and bodies match Python-generated expected files', () => {
  const cases: Array<[string, string, string]> = [
    ['pages', 'page.home', 'home.expected.json'],
    ['pages', 'page.about', 'about.expected.json'],
    ['posts', 'post.first', 'first-post.expected.json'],
  ];
  for (const [collection, id, expectedFile] of cases) {
    const items = loadCauldronCollection({
      ...FIXTURE_OPTS,
      collection,
      preview: true,
    });
    const item = items.find((i) => i.id === id);
    assert.ok(item, `Item ${id} not found`);
    const expected = JSON.parse(
      readFileSync(join(FIXTURES_DIR, 'expected', expectedFile), 'utf8'),
    );
    assert.equal(item.hash, expected.hash, `Hash mismatch for ${id}`);
    assert.equal(item.body, expected.body, `Body mismatch for ${id}`);
    assert.deepEqual(item.data, expected.data, `Data mismatch for ${id}`);
  }
});

test('parity: draft-page hash also matches Python', () => {
  const items = loadCauldronCollection({
    ...FIXTURE_OPTS,
    collection: 'pages',
    preview: true,
  });
  const draft = items.find((i) => i.id === 'page.draft');
  assert.ok(draft);
  const expected = JSON.parse(
    readFileSync(join(FIXTURES_DIR, 'expected', 'draft-page.expected.json'), 'utf8'),
  );
  assert.equal(draft.hash, expected.hash);
});

test('computeContentHash is deterministic', () => {
  const a = computeContentHash('id', 'coll', 'slug', 'published', 'schema', { a: 1 }, 'body');
  const b = computeContentHash('id', 'coll', 'slug', 'published', 'schema', { a: 1 }, 'body');
  assert.equal(a, b);
});

test('computeContentHash: data key order does not matter', () => {
  const a = computeContentHash('id', 'c', 's', 'p', 'sc', { a: 1, b: 2 }, '');
  const b = computeContentHash('id', 'c', 's', 'p', 'sc', { b: 2, a: 1 }, '');
  assert.equal(a, b);
});

test('duplicate ID throws', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cauldron-astro-'));
  const coll = join(dir, 'content', 'dupes');
  mkdirSync(coll, { recursive: true });
  writeFileSync(
    join(coll, 'a.md'),
    '---\nid: same\nslug: one\nstatus: published\nschema: pages\ntitle: A\n---\nA\n',
    'utf8',
  );
  writeFileSync(
    join(coll, 'b.md'),
    '---\nid: same\nslug: two\nstatus: published\nschema: pages\ntitle: B\n---\nB\n',
    'utf8',
  );
  assert.throws(() =>
    loadCauldronCollection({ siteRoot: dir, collection: 'dupes' }),
  );
});

test('duplicate slug throws', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cauldron-astro-'));
  const coll = join(dir, 'content', 'dupes');
  mkdirSync(coll, { recursive: true });
  writeFileSync(
    join(coll, 'a.md'),
    '---\nid: one\nslug: same\nstatus: published\nschema: pages\ntitle: A\n---\nA\n',
    'utf8',
  );
  writeFileSync(
    join(coll, 'b.md'),
    '---\nid: two\nslug: same\nstatus: published\nschema: pages\ntitle: B\n---\nB\n',
    'utf8',
  );
  assert.throws(() =>
    loadCauldronCollection({ siteRoot: dir, collection: 'dupes' }),
  );
});

test('path traversal blocked', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cauldron-astro-'));
  // With the default contentRoot of "content" the collection segment must climb
  // at least two levels to escape ``siteRoot``.
  assert.throws(() =>
    loadCauldronCollection({ siteRoot: dir, collection: '../../../etc' }),
  );
});

test('loader.load writes into the store and clears first', async () => {
  const loader = createCauldronContentLoader({
    ...FIXTURE_OPTS,
    collection: 'pages',
  });
  const entries: Array<{ id: string; data: Record<string, unknown>; body?: string }> = [];
  let cleared = false;
  const store = {
    set(entry: { id: string; data: Record<string, unknown>; body?: string }) {
      entries.push(entry);
      return true;
    },
    clear() {
      cleared = true;
    },
  };
  await loader.load({ store });
  assert.ok(cleared, 'clear() should be called');
  assert.ok(entries.length >= 2, 'should have loaded published items');
  const ids = entries.map((e) => e.id);
  assert.ok(ids.includes('page.home'));
  assert.ok(!ids.includes('page.draft'), 'draft excluded by default');

  // Front-matter fields must be at entry.data top level, not nested under entry.data.data
  const home = entries.find((e) => e.id === 'page.home');
  assert.ok(home, 'page.home entry not found');
  assert.equal(typeof (home.data as Record<string, unknown>).title, 'string', 'title should be at data.title');
  assert.equal((home.data as Record<string, unknown>).data, undefined, 'data.data should not exist');
  // Cauldron metadata available under _cauldron
  const cauldron = (home.data as Record<string, unknown>)._cauldron as Record<string, unknown>;
  assert.ok(cauldron, '_cauldron metadata should be present');
  assert.equal(cauldron.id, 'page.home');
});
