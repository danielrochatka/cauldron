"""Attachment service — create, extract, and read attachment records."""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_ATTACHMENT_TEXT_CHARS = 50_000
_MAX_ATTACHMENTS_PER_USER_PER_HOUR = 20

ALLOWED_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
})

ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md", ".markdown"})


class AttachmentValidationError(ValueError):
    pass


class AttachmentService:
    def create_from_bytes(
        self,
        *,
        owner,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> "AttachmentRecord":
        from .models import AttachmentRecord, ExtractionStatus

        self._validate_file(filename=filename, content_type=content_type, data=data)

        checksum = hashlib.sha256(data).hexdigest()

        record = AttachmentRecord(
            owner=owner,
            filename=filename[:255],
            content_type=content_type[:128],
            size_bytes=len(data),
            checksum_sha256=checksum,
            extraction_status=ExtractionStatus.PENDING,
        )

        self._extract_and_populate(record, data, filename=filename, content_type=content_type)
        record.save()
        return record

    def _validate_file(self, *, filename: str, content_type: str, data: bytes) -> None:
        import os

        if len(data) == 0:
            raise AttachmentValidationError("File is empty.")

        if len(data) > _MAX_FILE_SIZE_BYTES:
            mb = _MAX_FILE_SIZE_BYTES // (1024 * 1024)
            raise AttachmentValidationError(f"File exceeds maximum size of {mb} MB.")

        ext = os.path.splitext(filename.lower())[1]

        if content_type not in ALLOWED_CONTENT_TYPES and ext not in ALLOWED_EXTENSIONS:
            raise AttachmentValidationError(
                f"Unsupported file type: {content_type!r}. "
                f"Supported types: PDF, DOCX, TXT, Markdown."
            )

        # Basic magic byte validation
        if ext == ".pdf" or content_type == "application/pdf":
            if not data.startswith(b"%PDF"):
                raise AttachmentValidationError(
                    "File does not appear to be a valid PDF (missing PDF header)."
                )

    def _extract_and_populate(
        self, record, data: bytes, *, filename: str, content_type: str
    ) -> None:
        from .extractors import ExtractionError, extract_document
        from .models import ExtractionStatus

        try:
            result = extract_document(
                data, content_type, filename, max_chars=_MAX_ATTACHMENT_TEXT_CHARS
            )
            record.extraction_status = ExtractionStatus.EXTRACTED
            record.extracted_text = result.text
            record.word_count = result.word_count
            record.page_count = result.page_count
            record.section_headings = list(result.section_headings)
            record.truncated = result.truncated
            record.extractor_name = result.extractor
        except ExtractionError as exc:
            logger.warning("Attachment extraction failed for %r: %s", filename, exc)
            record.extraction_status = ExtractionStatus.FAILED
            record.extraction_error = str(exc)[:1000]
        except Exception as exc:
            logger.exception("Unexpected error extracting %r", filename)
            record.extraction_status = ExtractionStatus.FAILED
            record.extraction_error = f"Internal error: {type(exc).__name__}"

    def get_attachment(self, attachment_id: str, owner) -> "AttachmentRecord":
        from .models import AttachmentRecord
        try:
            return AttachmentRecord.objects.get(id=attachment_id, owner=owner)
        except AttachmentRecord.DoesNotExist:
            raise LookupError(f"Attachment {attachment_id!r} not found or not accessible.")


def get_attachment_service() -> AttachmentService:
    return AttachmentService()
