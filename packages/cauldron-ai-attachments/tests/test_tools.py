"""Unit tests for the attachments.read Admin AI tool."""
from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.django_db


def _make_user(username="tools-test-user"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    return user


def _ctx(user=None):
    from cauldron_ai_admin.tools import AdminAIToolContext
    return AdminAIToolContext(
        actor=user or _make_user(),
        run_id="test-run",
        correlation_id="test-corr",
    )


def _make_docx(text: str = "Jane Smith") -> bytes:
    from docx import Document
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _fresh_registry():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_ai_attachments.tools import register
    reg = AdminAIToolRegistry()
    register(reg)
    return reg


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_adds_tool():
    reg = _fresh_registry()
    entry = reg.get("attachments.read")
    assert entry is not None
    definition, handler = entry
    assert definition.name == "attachments.read"
    assert callable(handler)


def test_register_is_idempotent():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_ai_attachments.tools import register
    reg = AdminAIToolRegistry()
    register(reg)
    register(reg)  # must not raise
    assert reg.get("attachments.read") is not None


# ---------------------------------------------------------------------------
# Handler — success path
# ---------------------------------------------------------------------------

def test_handler_returns_extracted_text():
    from cauldron_ai_attachments.service import AttachmentService

    user = _make_user()
    docx = _make_docx("Carol Wang DevOps Engineer Kubernetes Terraform")
    record = AttachmentService().create_from_bytes(
        owner=user,
        filename="cv.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=docx,
    )

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(user), attachment_id=str(record.id))

    assert result.success is True
    assert result.data["filename"] == "cv.docx"
    assert "text" in result.data
    assert "Carol" in result.data["text"]
    assert result.data["word_count"] > 0


def test_handler_includes_provenance_fields():
    from cauldron_ai_attachments.service import AttachmentService

    user = _make_user()
    record = AttachmentService().create_from_bytes(
        owner=user,
        filename="note.txt",
        content_type="text/plain",
        data=b"Software skills: Python",
    )

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(user), attachment_id=str(record.id))

    data = result.data
    assert "attachment_id" in data
    assert "content_type" in data
    assert "size_bytes" in data
    assert "extracted_at" in data
    assert data["extraction_status"] == "extracted"


# ---------------------------------------------------------------------------
# Handler — error paths
# ---------------------------------------------------------------------------

def test_handler_not_found_returns_error():
    import uuid
    from cauldron_ai_admin.tools import AdminAIToolError

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(), attachment_id=str(uuid.uuid4()))

    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "attachment.not_found"


def test_handler_wrong_owner_returns_error():
    from cauldron_ai_attachments.service import AttachmentService
    from django.contrib.auth import get_user_model
    from cauldron_ai_admin.tools import AdminAIToolError

    User = get_user_model()
    owner = _make_user("owner-tools")
    other, _ = User.objects.get_or_create(username="other-tools")

    record = AttachmentService().create_from_bytes(
        owner=owner,
        filename="private.txt",
        content_type="text/plain",
        data=b"Private content",
    )

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(other), attachment_id=str(record.id))

    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "attachment.not_found"


def test_handler_failed_extraction_returns_error(db):
    from cauldron_ai_attachments.models import AttachmentRecord, ExtractionStatus
    from cauldron_ai_admin.tools import AdminAIToolError
    from django.contrib.auth import get_user_model
    import uuid

    User = get_user_model()
    user = _make_user()
    record = AttachmentRecord.objects.create(
        id=uuid.uuid4(),
        owner=user,
        filename="bad.pdf",
        content_type="application/pdf",
        size_bytes=100,
        checksum_sha256="a" * 64,
        extraction_status=ExtractionStatus.FAILED,
        extraction_error="Corrupt PDF structure",
    )

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(user), attachment_id=str(record.id))

    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "attachment.extraction_failed"
    assert "Corrupt PDF" in result.message


def test_handler_empty_attachment_id_returns_error():
    from cauldron_ai_admin.tools import AdminAIToolError

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    result = handler(_ctx(), attachment_id="")

    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.invalid_arguments"
