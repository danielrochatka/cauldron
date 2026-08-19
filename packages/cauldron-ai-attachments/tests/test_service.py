"""Unit tests for AttachmentService."""
from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.django_db


def _make_user(username="svc-test-user"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    return user


def _make_minimal_pdf(text: str = "Jane Smith Engineer") -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode("latin-1")
    stream_len = len(stream)
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    obj4 = (
        b"4 0 obj\n<< /Length "
        + str(stream_len).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n"
    )
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    header = b"%PDF-1.4\n"
    body = header + obj1 + obj2 + obj3 + obj4 + obj5
    o1, o2, o3, o4, o5 = (
        len(header),
        len(header) + len(obj1),
        len(header) + len(obj1) + len(obj2),
        len(header) + len(obj1) + len(obj2) + len(obj3),
        len(header) + len(obj1) + len(obj2) + len(obj3) + len(obj4),
    )
    xref_offset = len(body)
    xref = (
        b"xref\n0 6\n"
        + b"0000000000 65535 f \n"
        + f"{o1:010d} 00000 n \n".encode()
        + f"{o2:010d} 00000 n \n".encode()
        + f"{o3:010d} 00000 n \n".encode()
        + f"{o4:010d} 00000 n \n".encode()
        + f"{o5:010d} 00000 n \n".encode()
    )
    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return body + xref + trailer


def _make_docx(text: str = "Jane Smith") -> bytes:
    from docx import Document
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# create_from_bytes — PDF
# ---------------------------------------------------------------------------

def test_create_from_bytes_pdf_success():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.models import ExtractionStatus

    svc = AttachmentService()
    user = _make_user()
    pdf = _make_minimal_pdf("Alice Engineer Python")
    record = svc.create_from_bytes(
        owner=user,
        filename="cv.pdf",
        content_type="application/pdf",
        data=pdf,
    )

    assert record.pk is not None
    assert record.filename == "cv.pdf"
    assert record.extraction_status == ExtractionStatus.EXTRACTED
    assert record.word_count > 0
    assert record.checksum_sha256  # non-empty SHA-256
    assert len(record.checksum_sha256) == 64


def test_create_from_bytes_docx_success():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.models import ExtractionStatus

    svc = AttachmentService()
    user = _make_user()
    docx = _make_docx("Bob Lee Senior Dev React")
    record = svc.create_from_bytes(
        owner=user,
        filename="resume.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=docx,
    )
    assert record.extraction_status == ExtractionStatus.EXTRACTED
    assert "Bob" in record.extracted_text


def test_create_from_bytes_plain_text_success():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.models import ExtractionStatus

    svc = AttachmentService()
    user = _make_user()
    record = svc.create_from_bytes(
        owner=user,
        filename="notes.txt",
        content_type="text/plain",
        data=b"Skills: Python, Django\nExperience: 5 years",
    )
    assert record.extraction_status == ExtractionStatus.EXTRACTED
    assert "Python" in record.extracted_text


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_rejects_empty_file():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError
    user = _make_user()
    with pytest.raises(AttachmentValidationError, match="empty"):
        AttachmentService().create_from_bytes(
            owner=user, filename="empty.pdf", content_type="application/pdf", data=b""
        )


def test_rejects_oversized_file():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError
    user = _make_user()
    big = b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(AttachmentValidationError, match="exceeds maximum size"):
        AttachmentService().create_from_bytes(
            owner=user, filename="big.pdf", content_type="application/pdf", data=big
        )


def test_rejects_unsupported_content_type():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError
    user = _make_user()
    with pytest.raises(AttachmentValidationError, match="Unsupported file type"):
        AttachmentService().create_from_bytes(
            owner=user, filename="image.png", content_type="image/png", data=b"\x89PNG\r\n\x1a\n"
        )


def test_rejects_pdf_missing_header():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError
    user = _make_user()
    with pytest.raises(AttachmentValidationError, match="valid PDF"):
        AttachmentService().create_from_bytes(
            owner=user, filename="fake.pdf", content_type="application/pdf",
            data=b"This is not a PDF"
        )


# ---------------------------------------------------------------------------
# get_attachment
# ---------------------------------------------------------------------------

def test_get_attachment_own_record():
    from cauldron_ai_attachments.service import AttachmentService
    svc = AttachmentService()
    user = _make_user()
    record = svc.create_from_bytes(
        owner=user, filename="r.txt", content_type="text/plain", data=b"Hello"
    )
    fetched = svc.get_attachment(str(record.id), user)
    assert fetched.id == record.id


def test_get_attachment_not_found_raises():
    from cauldron_ai_attachments.service import AttachmentService
    svc = AttachmentService()
    user = _make_user()
    import uuid
    with pytest.raises(LookupError):
        svc.get_attachment(str(uuid.uuid4()), user)


def test_get_attachment_wrong_owner_raises():
    from cauldron_ai_attachments.service import AttachmentService
    from django.contrib.auth import get_user_model
    User = get_user_model()

    svc = AttachmentService()
    owner = _make_user("owner-user")
    other, _ = User.objects.get_or_create(username="other-user")

    record = svc.create_from_bytes(
        owner=owner, filename="secret.txt", content_type="text/plain", data=b"Secret"
    )
    with pytest.raises(LookupError):
        svc.get_attachment(str(record.id), other)


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_after_threshold():
    from cauldron_ai_attachments.service import (
        AttachmentService,
        AttachmentValidationError,
        _MAX_ATTACHMENTS_PER_USER_PER_HOUR,
    )
    from cauldron_ai_attachments.models import AttachmentRecord
    from django.utils import timezone

    user = _make_user("rate-limit-user")
    svc = AttachmentService()

    # Seed the DB with records up to the limit (without going through the full
    # service to keep the test fast — we're testing the counter, not extraction).
    one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
    for i in range(_MAX_ATTACHMENTS_PER_USER_PER_HOUR):
        AttachmentRecord.objects.create(
            owner=user,
            filename=f"file-{i}.txt",
            content_type="text/plain",
            size_bytes=10,
            checksum_sha256="a" * 64,
            extraction_status="extracted",
            extracted_text="text",
            word_count=1,
        )

    with pytest.raises(AttachmentValidationError, match="Upload limit"):
        svc.create_from_bytes(
            owner=user,
            filename="overflow.txt",
            content_type="text/plain",
            data=b"extra",
        )
