# Cauldron Technical Admin Shell

The Technical Admin shell (`cauldron_django_admin`) provides a product-branded administration interface for Cauldron. It sits alongside the standard Django admin (`/admin/`) and is the correct term for referring to the `/admin/` interface in user-visible UI.

---

## Shell architecture

### Routes

| Pattern | View | Name |
|---|---|---|
| `/cauldron/` | `dashboard_view` | `cauldron:dashboard` |
| `/cauldron/modules/` | `modules_view` | `cauldron:modules` |
| `/cauldron-overrides/<scope>/<path:rel_path>` | `CSSOverrideView` | `cauldron-override-css` |

Register shell URLs in your root URLconf:

```python
from cauldron_django_admin.urls import get_admin_urls, get_cauldron_urls

urlpatterns = get_admin_urls() + get_cauldron_urls() + [...]
```

### Template contract

All Cauldron shell pages extend `cauldron_admin/base.html`. The base template provides:

- CSS custom-property tokens (`tokens.css`)
- CSS reset, layout, component, form, table, utility, and responsive stylesheets
- Site-owned CSS override injection (via `{% get_override_css_urls "admin" %}`)
- Sidebar navigation (resolved URLs and active-state computed in Python)
- Skip-to-content link
- Accessible nav drawer (focus management, Escape key close)

Blocks available to child templates: `body_classes`, `head_title`, `title`, `extra_css`, `extra_head`, `page_title`, `page_description`, `page_actions`, `content`, `extra_js`, `cauldron_version`.

---

## Navigation contract

### Registration

Register sections and items in `AppConfig.ready()`:

```python
from cauldron_django_admin.navigation import (
    get_navigation_registry, AdminNavigationSection, AdminNavigationItem
)

registry = get_navigation_registry()
registry.register_section(AdminNavigationSection(key="myapp", label="My App", order=100))
registry.register_item(AdminNavigationItem(
    key="myapp.index",
    label="Overview",
    url_name="myapp:index",
    section="myapp",
    order=10,
    permission="myapp.view_overview",
    url_prefix="/myapp/",
    description="Overview of my app",
))
```

### Validation

- `key`: `[a-zA-Z0-9._-]{1,128}` — must be unique per kind
- `label`: 1–256 characters
- `permission`: `app_label.codename` format or empty string (public item)
- `order`: must be an `int`
- `description`: ≤256 characters
- Section must be registered **before** items that reference it

### Idempotency

Registering an identical section or item a second time is a no-op (idempotent). Registering a different value with the same key raises `ValueError`.

### Sections and ordering

Items are sorted by `(section.order, item.order, item.label)`.

### URL resolution

`NavigationRegistry.resolve_url(item)` calls `reverse(item.url_name)` safely and returns `"#"` on `NoReverseMatch`. The `get_grouped_nav(user, request)` method resolves all URLs and computes `is_active` in Python before returning nav dicts to the template.

---

## Stable CSS variables and selectors

Stable CSS custom properties (defined in `tokens.css`):

- `--cui-color-primary`, `--cui-color-primary-dark`
- `--cui-color-surface`, `--cui-color-border`
- `--cui-color-text`, `--cui-color-text-muted`
- `--cui-space-*`, `--cui-radius-*`, `--cui-font-*`

Stable BEM selectors (do not target internal modifier classes):

- `.cui-shell`, `.cui-sidebar`, `.cui-main`
- `.cui-card`, `.cui-button`, `.cui-badge`
- `.cui-table`, `.cui-table-container`
- `.cui-nav__item--active` (active navigation item)

---

## Override directory

### Scopes

Two scopes are supported: `admin` and `pages`. CSS files are organized under `<override-root>/admin/` and `<override-root>/pages/`.

### Initialization

```bash
python manage.py cauldron_ui_init           # create override root with scaffold files
python manage.py cauldron_ui_init --force   # overwrite existing scaffold files
python manage.py cauldron_ui_init --check   # validate without creating files
```

### .gitignore

The override directory is initialized with a `.gitignore` that ignores all files except itself (`*\n!.gitignore\n`). Site-generated CSS overrides should not be committed to source control.

### Serving route

Override CSS is served at `/cauldron-overrides/<scope>/<path:rel_path>`. The `<path:rel_path>` converter captures nested paths. Files are validated through `UIOverrideStore` before being served — traversal, symlink escape, non-CSS, and hidden-component paths all return an empty `200` or `403`.

### Configuration

```python
# settings.py
CAULDRON_UI_OVERRIDES_DIR = BASE_DIR / "cauldron-overrides"  # optional, defaults to BASE_DIR/cauldron-overrides
```

---

## AI style lifecycle

CSS proposals flow through the following states:

```
proposed → approved → applied
         ↘ rejected
         (approved) → conflicted  (if file changed under us)
         (approved) → failed      (on store error)
```

### Service API

```python
from cauldron_ai_admin.style_service import get_style_service

service = get_style_service()
proposal = service.create_proposal(scope="admin", target_path="custom.css", proposed_content="...", description="...")
service.approve(proposal, reviewed_by=request.user)
service.apply(proposal, applied_by=request.user)
service.reject(proposal, reviewed_by=request.user)
```

All state transitions are wrapped in `transaction.atomic()` with `select_for_update()`. Sequence numbers for audit events are computed via `MAX(sequence) + 1` inside the transaction.

Error summaries store only the exception class name (never raw exception text) to prevent path/data leakage.

---

## Upgrade preservation guidance

When upgrading `cauldron-django-admin`:

1. Run `python manage.py migrate` to apply any new migrations.
2. Run `python manage.py cauldron_ui_init --check` to verify the override directory is still valid.
3. Run `python manage.py check` to catch any new system check failures.
4. Site-owned CSS files in the override directory are never touched by upgrades.

---

## Technical Admin's role

The Technical Admin shell is the primary interface for:

- Browsing module status and capability graph
- Reviewing and approving AI-proposed style changes
- Browsing content operations and audit logs

It is distinct from the Django admin (`/admin/`), which handles raw database management. The product UI should always say "Technical Admin" — never "Django admin" — when referring to the `/admin/` interface.
