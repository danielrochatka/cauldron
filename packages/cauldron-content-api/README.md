# cauldron-content-api

The Cauldron Content API module exposes a JSON HTTP API for the content control plane. All content read, proposal, validation, approval, application, rollback, preview, and audit operations are available through versioned REST endpoints.

## Endpoints

- `GET /collections/` — list all content collections
- `GET /collections/<collection>/items/` — list items in a collection
- `GET /collections/<collection>/items/<item_id>/` — get a single item
- `GET /change-requests/` — list change requests
- `POST /change-requests/` — create a new change request
- `GET /change-requests/<id>/` — get change request detail
- `GET /change-requests/<id>/preview/` — preview proposed changes
- `GET /change-requests/<id>/audit/` — get audit history
- `POST /change-requests/<id>/validate/` — validate a change request
- `POST /change-requests/<id>/approve/` — approve a change request
- `POST /change-requests/<id>/reject/` — reject a change request
- `POST /change-requests/<id>/apply/` — apply a change request
- `POST /change-requests/<id>/rollback/` — roll back an applied change request

## URL mounting

```python
# urls.py
urlpatterns = [
    path("cauldron/api/v1/", include("cauldron_content_api.urls")),
]
```
