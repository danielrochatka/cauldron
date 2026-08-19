"""Tests for AttachmentUploadView."""
from __future__ import annotations

import io
import unittest.mock as mock

import pytest

pytestmark = pytest.mark.django_db


def _make_user(username="view-test-user"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"is_superuser": True, "is_staff": True},
    )
    return user


def _make_docx() -> bytes:
    from docx import Document
    doc = Document()
    doc.add_paragraph("Test content from view test")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Size pre-check (Finding 6) — must reject before reading into memory
# ---------------------------------------------------------------------------

def test_oversized_file_rejected_before_read():
    """View must check uploaded_file.size and return 413 before calling .read()."""
    from cauldron_ai_attachments.service import _MAX_FILE_SIZE_BYTES
    from cauldron_ai_attachments.views import AttachmentUploadView
    from django.test import RequestFactory
    from django.http import QueryDict
    from django.utils.datastructures import MultiValueDict

    user = _make_user("size-check-user")
    factory = RequestFactory()

    # Build a mock uploaded file whose .size exceeds the limit.
    # .read() must NOT be called if the size check fires first.
    upload = mock.MagicMock()
    upload.name = "big.txt"
    upload.content_type = "text/plain"
    upload.size = _MAX_FILE_SIZE_BYTES + 1
    upload.read = mock.Mock(
        side_effect=AssertionError(".read() was called before the size pre-check")
    )

    # Build the request without going through multipart encoding (which would
    # call .read() on the mock during request construction). Set _files
    # directly to bypass the WSGIRequest.FILES property setter restriction.
    request = factory.post("/upload/", content_type="multipart/form-data")
    request.user = user
    request._files = MultiValueDict({"file": [upload]})

    response = AttachmentUploadView.as_view()(request)
    assert response.status_code == 413
    upload.read.assert_not_called()


# ---------------------------------------------------------------------------
# Missing file field
# ---------------------------------------------------------------------------

def test_missing_file_field_returns_400():
    """POST without a 'file' field must return 400."""
    from cauldron_ai_attachments.views import AttachmentUploadView
    from django.test import RequestFactory

    user = _make_user("no-file-user")
    factory = RequestFactory()
    request = factory.post("/upload/", data={})
    request.user = user
    response = AttachmentUploadView.as_view()(request)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Valid upload via Django test Client (full URL routing)
# ---------------------------------------------------------------------------

def test_valid_docx_upload_returns_201_with_attachment_id():
    """A valid DOCX upload must return 201 with attachment_id."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    user = _make_user("upload-user")
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)

    docx = _make_docx()
    upload = SimpleUploadedFile(
        "resume.docx",
        docx,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response = client.post(
        "/cauldron/admin/ai/attachments/upload/",
        data={"file": upload},
    )
    assert response.status_code == 201
    data = response.json()
    assert "attachment_id" in data
    assert data["attachment_id"]
    assert "filename" in data
    assert "status" in data


def test_valid_plaintext_upload_returns_201():
    """Plain text files must upload successfully."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    user = _make_user("plaintext-upload-user")
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)

    upload = SimpleUploadedFile(
        "notes.txt",
        b"Skills: Python, Django\nExperience: 5 years",
        content_type="text/plain",
    )
    response = client.post(
        "/cauldron/admin/ai/attachments/upload/",
        data={"file": upload},
    )
    assert response.status_code == 201
    data = response.json()
    assert "attachment_id" in data


def test_unauthenticated_post_is_rejected():
    """Unauthenticated requests must be rejected with redirect or 403."""
    from django.test import Client

    client = Client(enforce_csrf_checks=False)
    response = client.post(
        "/cauldron/admin/ai/attachments/upload/",
        data={"file": io.BytesIO(b"hello")},
    )
    assert response.status_code in (302, 403)
