# Cauldron Admin AI Site Builder

The Admin AI Site Builder lets you attach a resume (PDF, DOCX, or text file), point at a reference website, and ask the AI to draft a personal site — content and styling — based on both inputs.

## Enabling the modules

Add all three modules to `CAULDRON_MODULES` in your site's `settings.py`:

```python
CAULDRON_MODULES = {
    # ... existing modules ...
    "cauldron.ai.attachments": {},   # resume/brief ingestion
    "cauldron.ai.web": {},           # public URL → design characteristics
    "cauldron.ai.sitebuilder": {},   # system-check glue
}
```

All three packages must be installed:

```
pip install cauldron-ai-attachments cauldron-ai-web cauldron-ai-site-builder
```

The attachment upload endpoint is also registered automatically when `cauldron-ai-attachments` is installed. No extra URL configuration is required beyond enabling the module.

## Supported file formats

| Format | MIME type | Notes |
|--------|-----------|-------|
| PDF | `application/pdf` | Up to 100 pages, 10 MB max |
| DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | python-docx required |
| Plain text | `text/plain` | UTF-8; Markdown headings extracted |
| Markdown | `text/plain`, `.md` | Same as plain text |

**Not supported**: legacy `.doc` (Word 97–2003 format), images, spreadsheets, ZIP files.

## Attachment workflow

1. Open **Admin AI** in the Cauldron shell.
2. Click **Choose file** in the Attachments section and select your PDF, DOCX, or TXT file.
3. The file uploads automatically — wait for **"ready"** to appear next to the filename.
4. Type your request in the text area (e.g. *"Build a personal site based on my resume and the design at https://example.com"*).
5. Click **Send Request**.

The browser automatically associates your uploaded attachment IDs with the Admin AI request. You never copy or paste file IDs.

During the AI run, the model retrieves attachment content through the `attachments.read` tool — the same permission-aware, audited tool loop used for all Admin AI tool calls. The attachment content enters the conversation as a tool result rather than being silently injected into the original request. Every retrieval is recorded in the `AdminAIToolInvocation` audit log.

**Rate limit**: at most 20 files per user per hour.

## URL inspection

The `web.inspect_url` tool fetches a public HTTP/HTTPS URL and extracts:

- Page title, headings (h1–h4), and navigation items
- Font families and colour hints
- CSS custom properties (variables)
- Border-radius and spacing hints
- Layout patterns (cards)
- Visible text summary (up to 5 000 characters)

Only publicly accessible URLs are permitted. Private network addresses (RFC 1918, loopback, link-local, IPv6 ULA) and `localhost` are blocked. Redirects are validated before being followed.

## Security boundaries

- **SSRF protection**: DNS is resolved before any connection attempt. Every HTTP redirect target is validated before being followed. The maximum number of redirects is 5.
- **File validation**: MIME type, file extension, magic bytes (PDF header), and file size are checked before reading content into memory.
- **Permission gate**: file upload requires `cauldron_ai_attachments.upload_attachment`; reading an attachment in a tool call requires `cauldron_ai_attachments.read_attachment`.

## Content and style proposal flow

The AI uses the registered tool set to draft a site:

1. **`attachments.read`** — retrieves the extracted text from an uploaded file via the normal permission-aware tool loop. Each call creates an audit record.
2. **`web.inspect_url`** — fetches the reference site's design characteristics.
3. **`content.create_proposal`** — proposes new or updated page content (title, body).
4. **`ui.styles.create_proposal`** — proposes a CSS theme derived from the reference site.
5. **`site.prepare_change_set`** — queues the content and style proposals for review.

After the AI run completes, a human operator reviews the proposals in the **Style Proposals** and **Content** sections of the Cauldron shell. No changes are published without explicit approval.

## Preview and publication lifecycle

Style proposals targeted at `pages` scope go through the full publication pipeline:

```
create_proposal → [human review] → approve → [prepare_change_set] →
preview → [human review] → publish → CSS written to disk
```

Key guarantees (validated by the acceptance test suite):

- Creating or approving a CSS proposal does **not** write any file to disk.
- The CSS file is written atomically during `publish()` (Step 2.5 of `SiteChangeSet`).
- If the Astro build fails, the CSS write is rolled back and the live site is restored byte-for-byte.
- `mark_style_applied()` is a database-only operation; it never writes to disk.

## Deferred capabilities

The following capabilities are tracked for future development and are **not** included in the MVP:

- Multi-page proposals (the current flow produces a single homepage draft).
- Image upload and AI-assisted image alt-text.
- Iterative refinement (ask the AI to revise a specific section without rebuilding everything).
- Scheduled or automated re-sync when the reference site changes.
