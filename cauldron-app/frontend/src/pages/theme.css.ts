import { loadManifest } from '../lib/manifest';

const DEFAULT_CSS = `/* Cauldron public site – default theme */
:root {
  --color-primary: #2563eb;
  --color-text: #1f2937;
  --color-bg: #ffffff;
  --color-nav-bg: #f8fafc;
  --color-nav-border: #e2e8f0;
  --font-sans: system-ui, -apple-system, sans-serif;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  color: var(--color-text);
  background: var(--color-bg);
  line-height: 1.6;
}
.cauldron-preview-banner {
  background: #fef3c7;
  border-bottom: 2px solid #d97706;
  color: #92400e;
  font-size: 0.875rem;
  font-weight: 600;
  padding: 0.5rem 1rem;
  text-align: center;
}
nav[aria-label="Main navigation"] {
  background: var(--color-nav-bg);
  border-bottom: 1px solid var(--color-nav-border);
}
nav[aria-label="Main navigation"] ul {
  display: flex;
  gap: 0;
  list-style: none;
  max-width: 64rem;
  margin: 0 auto;
  padding: 0 1.5rem;
}
nav[aria-label="Main navigation"] a {
  display: block;
  padding: 0.75rem 1rem;
  color: var(--color-text);
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 500;
}
nav[aria-label="Main navigation"] a:hover,
nav[aria-label="Main navigation"] a:focus-visible {
  color: var(--color-primary);
  outline: 2px solid var(--color-primary);
  outline-offset: -2px;
}
nav[aria-label="Main navigation"] a[aria-current="page"] {
  color: var(--color-primary);
  border-bottom: 2px solid currentColor;
}
main {
  max-width: 64rem;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}
h1 { font-size: 2rem; margin-bottom: 1rem; }
.summary { font-size: 1.125rem; color: #6b7280; margin-bottom: 1.5rem; }
.body p { margin-bottom: 1rem; }
.body h2 { font-size: 1.5rem; margin: 1.5rem 0 0.75rem; }
.body h3 { font-size: 1.25rem; margin: 1.25rem 0 0.5rem; }
.body ul, .body ol { margin: 0 0 1rem 1.5rem; }
.body a { color: var(--color-primary); }`;

export async function GET() {
  let css = DEFAULT_CSS;
  try {
    const manifest = loadManifest();
    if (typeof manifest?.theme?.css_content === 'string' && manifest.theme.css_content) {
      css = manifest.theme.css_content;
    }
  } catch {
    // No manifest / bad manifest — fall through to default so the theme
    // route always responds with valid CSS.
  }
  return new Response(css, {
    headers: { 'Content-Type': 'text/css; charset=utf-8' },
  });
}
