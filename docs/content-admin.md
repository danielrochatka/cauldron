# Content Django Admin

The `cauldron-admin-content` package registers `ContentChangeRequest` and `ContentAuditEvent` with the Django Admin and provides additional views for content browsing and proposal creation.

## Admin registrations

### ContentChangeRequest

- All fields are read-only in the admin.
- Adding and deleting are disabled.
- Custom action endpoints (POST only) are registered as admin views:
  - `/<pk>/validate/` — validate the change request
  - `/<pk>/approve/` — approve the change request
  - `/<pk>/reject/` — reject the change request (reads `reason` from POST body)
  - `/<pk>/apply/` — apply the change request
  - `/<pk>/rollback/` — roll back the change request

All actions delegate to `ContentOperationService` — the admin does not bypass permissions.

### ContentAuditEvent

- Read-only. No add, change, or delete permitted.
- Displays event type, sequence, actor, timestamps, and detail JSON.

## Additional views

Mount `cauldron_admin_content.urls` alongside the admin:

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("admin/cauldron/", include("cauldron_admin_content.urls")),
]
```

### Content Browser (`/admin/cauldron/content-browser/`)

Browse published and draft content items by collection. Requires staff access.

### Content Proposal (`/admin/cauldron/content-proposal/`)

Form-based interface for creating a content proposal. Submits to `ContentOperationService.create_change_request`. Requires staff access and `propose_content_changes` permission.

## Templates

Templates extend `admin/base_site.html` and are located in:

```
cauldron_admin_content/templates/cauldron_admin_content/
  content_browser.html
  content_proposal.html
```
