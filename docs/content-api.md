# Content HTTP API

The `cauldron-content-api` package exposes a JSON HTTP API for the content control plane.

## URL mounting

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("cauldron/api/v1/", include("cauldron_content_api.urls")),
]
```

## Authentication

The API uses Django's session authentication. All endpoints require `is_authenticated`. Unauthenticated requests receive `401 Unauthorized`.

## CSRF

For state-changing endpoints (POST), include the Django CSRF token in the `X-CSRFToken` header when calling from a browser. From server-to-server clients using session cookies, also include the token. API clients using token-based auth should add `@csrf_exempt` to their own middleware stack and use a separate token auth backend.

## Response envelope

All responses use a consistent envelope:

**Success:**
```json
{
  "data": { ... },
  "meta": { "total": 10, "limit": 20, "offset": 0 }
}
```

**Error:**
```json
{
  "error": {
    "code": "not_found",
    "message": "Item 'home' not found in collection 'pages'.",
    "details": []
  }
}
```

## Endpoints

| Method | URL | Permission | Description |
|---|---|---|---|
| GET | `/collections/` | `view_published_content` | List all content collections |
| GET | `/collections/<collection>/items/` | `view_published_content` | List items in a collection |
| GET | `/collections/<collection>/items/<item_id>/` | `view_published_content` | Get a single item (ETag: content hash) |
| GET | `/change-requests/` | `view_published_content` | List change requests |
| POST | `/change-requests/` | `propose_content_changes` | Create a change request |
| GET | `/change-requests/<id>/` | `view_published_content` | Get change request detail (ETag: version) |
| GET | `/change-requests/<id>/preview/` | `view_published_content` | Preview proposed changes |
| GET | `/change-requests/<id>/audit/` | `view_content_audit` | Get audit history |
| POST | `/change-requests/<id>/validate/` | `validate_content_changes` | Validate the change request |
| POST | `/change-requests/<id>/approve/` | `approve_content_changes` | Approve the change request |
| POST | `/change-requests/<id>/reject/` | `reject_content_changes` | Reject the change request |
| POST | `/change-requests/<id>/apply/` | `apply_content_changes` | Apply changes to the repository |
| POST | `/change-requests/<id>/rollback/` | `rollback_content_changes` | Roll back applied changes |

## Pagination

List endpoints support `?limit=20&offset=0` query parameters. `limit` is capped at 100.

## ETags

`GET /collections/<collection>/items/<item_id>/` returns `ETag: "<content_hash>"`.
`GET /change-requests/<id>/` returns `ETag: "<request_version>"`.

## Error codes

| Code | HTTP status | Meaning |
|---|---|---|
| `auth.not_authenticated` | 401 | Not logged in |
| `auth.permission_denied` | 403 | Insufficient permissions |
| `not_found` | 404 | Resource not found |
| `conflict.version` | 409 | Optimistic concurrency conflict |
| `lifecycle.invalid_transition` | 409 | Invalid lifecycle transition |
| `validation.failed` | 422 | Structural validation failed |
| `operations.too_many` | 400 | Exceeded max_operations_per_change_set |
| `operations.invalid_kind` | 400 | Unknown operation kind |
| `approval.self_approval_denied` | 403 | Self-approval not permitted |
| `application.exception` | 400 | Repository application raised an exception |
| `application.conflicts` | 400 | Content hash conflicts detected |
| `rollback.failed` | 400 | Rollback could not be completed |
| `internal.unexpected_error` | 500 | Unhandled exception |
