"""Models for attachment records."""
from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.db import models


class ExtractionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    EXTRACTED = "extracted", "Extracted"
    FAILED = "failed", "Failed"
    UNSUPPORTED = "unsupported", "Unsupported format"


class AttachmentRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="ai_attachments",
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128)
    size_bytes = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    extraction_status = models.CharField(
        max_length=32,
        choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
    )
    extracted_text = models.TextField(blank=True)
    extraction_error = models.TextField(blank=True)
    word_count = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    section_headings = models.JSONField(default=list)
    truncated = models.BooleanField(default=False)
    extractor_name = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "cauldron_ai_attachments"
        ordering = ["-created_at"]
        permissions = [
            ("upload_attachment", "Can upload Admin AI attachments"),
            ("read_attachment", "Can read Admin AI attachment content"),
        ]

    def __str__(self) -> str:
        return f"{self.filename} ({self.extraction_status})"
