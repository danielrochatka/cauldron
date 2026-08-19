"""Views for cauldron_ai_attachments — file upload endpoint."""
from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

logger = logging.getLogger(__name__)

_UPLOAD_PERM = "cauldron_ai_attachments.upload_attachment"


@method_decorator([login_required, permission_required(_UPLOAD_PERM, raise_exception=True)], name="dispatch")
class AttachmentUploadView(View):
    """Accept a multipart/form-data POST with a 'file' field.

    CSRF protection is enforced by Django's middleware. Callers must include
    the CSRF token in their request (the X-CSRFToken header or csrfmiddlewaretoken
    form field), exactly as the Admin AI page does.

    Returns JSON: {"attachment_id": "<uuid>", "filename": "...", "status": "..."}
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return JsonResponse({"error": "No file uploaded. Use field name 'file'."}, status=400)

        from .service import _MAX_FILE_SIZE_BYTES
        if uploaded_file.size > _MAX_FILE_SIZE_BYTES:
            mb = _MAX_FILE_SIZE_BYTES // (1024 * 1024)
            return JsonResponse(
                {"error": f"File exceeds maximum size of {mb} MB."},
                status=413,
            )

        filename = uploaded_file.name or "upload"
        content_type = uploaded_file.content_type or "application/octet-stream"
        data = uploaded_file.read()

        try:
            from .service import AttachmentValidationError, get_attachment_service
            svc = get_attachment_service()
            record = svc.create_from_bytes(
                owner=request.user,
                filename=filename,
                content_type=content_type,
                data=data,
            )
        except AttachmentValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        except Exception:
            logger.exception("Attachment creation failed for user %s", request.user.pk)
            return JsonResponse({"error": "Internal error processing attachment."}, status=500)

        return JsonResponse(
            {
                "attachment_id": str(record.id),
                "filename": record.filename,
                "status": record.extraction_status,
                "word_count": record.word_count,
                "page_count": record.page_count,
            },
            status=201,
        )
