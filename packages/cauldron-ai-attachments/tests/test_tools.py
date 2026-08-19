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


# ---------------------------------------------------------------------------
# Byte-safe truncation (ARF2)
# ---------------------------------------------------------------------------

def test_truncate_to_bytes_ascii_no_truncation():
    from cauldron_ai_attachments.tools import _truncate_to_bytes
    text = "hello world"
    assert _truncate_to_bytes(text, 100) == text


def test_truncate_to_bytes_ascii_truncation():
    from cauldron_ai_attachments.tools import _truncate_to_bytes
    text = "a" * 200
    result = _truncate_to_bytes(text, 100)
    assert result == "a" * 100
    assert len(result.encode("utf-8")) == 100


def test_truncate_to_bytes_cjk_does_not_split_sequence():
    from cauldron_ai_attachments.tools import _truncate_to_bytes
    # Each CJK char is 3 UTF-8 bytes; limit to 10 bytes — only 3 full chars fit.
    text = "你好世界" * 10
    result = _truncate_to_bytes(text, 10)
    assert len(result.encode("utf-8")) <= 10
    result.encode("utf-8")  # must not raise — no split sequence


def test_truncate_to_bytes_emoji_does_not_split_sequence():
    from cauldron_ai_attachments.tools import _truncate_to_bytes
    # Each emoji is 4 UTF-8 bytes; limit to 10 bytes — 2 full emojis (8 bytes).
    text = "😀" * 10
    result = _truncate_to_bytes(text, 10)
    assert len(result.encode("utf-8")) <= 10
    result.encode("utf-8")  # must not raise


def test_handler_text_bounded_by_bytes_not_characters(db):
    """Handler must truncate by encoded bytes, not character count."""
    from cauldron_ai_attachments.models import AttachmentRecord, ExtractionStatus
    from cauldron_ai_attachments.tools import _MAX_TEXT_BYTES
    from cauldron_ai_admin.tools import AdminAIToolContext
    from django.contrib.auth import get_user_model
    import uuid

    # CJK chars are 3 bytes each. Build text where char count is within the old
    # 40 000-char limit but byte count exceeds _MAX_TEXT_BYTES.
    # _MAX_TEXT_BYTES is 65024; 65024 // 3 = 21674 full CJK chars.
    # Add 200 extra chars to guarantee the byte limit is exceeded.
    chars_at_limit = _MAX_TEXT_BYTES // 3
    text = "你" * (chars_at_limit + 200)

    User = get_user_model()
    user, _ = User.objects.get_or_create(username="byte-trunc-test")
    record = AttachmentRecord.objects.create(
        id=uuid.uuid4(),
        owner=user,
        filename="cjk.txt",
        content_type="text/plain",
        size_bytes=len(text.encode("utf-8")),
        checksum_sha256="c" * 64,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_text=text,
    )

    reg = _fresh_registry()
    _, handler = reg.get("attachments.read")
    ctx = AdminAIToolContext(actor=user, run_id="r", correlation_id="c")
    result = handler(ctx, attachment_id=str(record.id))

    assert result.success is True
    assert result.data["truncated"] is True
    returned_bytes = len(result.data["text"].encode("utf-8"))
    assert returned_bytes <= _MAX_TEXT_BYTES
    # Verify no multibyte sequence was split
    result.data["text"].encode("utf-8")
