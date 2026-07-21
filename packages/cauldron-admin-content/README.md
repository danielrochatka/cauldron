# cauldron-admin-content

Django Admin integration for the Cauldron content control plane. Provides:

- Read-only admin views for `ContentChangeRequest` and `ContentAuditEvent`
- Lifecycle action endpoints (validate, approve, reject, apply, rollback) accessible from the admin detail page
- Content browser view to explore published and draft content
- Content proposal form for creating change requests directly from the admin

All operations delegate to `ContentOperationService` — the admin does not bypass authorization.

## URL mounting

```python
# urls.py (alongside admin.site.urls)
urlpatterns = [
    path("admin/", admin.site.urls),
    path("admin/cauldron/", include("cauldron_admin_content.urls")),
]
```
