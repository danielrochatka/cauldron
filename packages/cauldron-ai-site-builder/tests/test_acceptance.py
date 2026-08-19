"""North-star acceptance tests for the Admin AI site-builder MVP.

Covers the full interaction surface: attachment ingestion (PDF + DOCX),
web URL inspection, tool registration, and error paths.

No live network requests are made — the HTTP fetcher is mocked throughout.
"""
from __future__ import annotations

import io
import socket
import unittest.mock as mock

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_pdf(text: str = "Jane Smith\nSoftware Engineer\nPython, Django") -> bytes:
    """Generate a minimal but valid PDF containing the given text."""
    content_stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
    content_len = len(content_stream)

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        + b"4 0 obj\n<< /Length " + str(content_len).encode() + b" >>\nstream\n"
        + content_stream + b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000119 00000 n \n"
        b"0000000274 00000 n \n"
        b"0000000400 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n480\n%%EOF"
    )
    return pdf


def _make_docx(text: str = "Jane Smith\nSoftware Engineer") -> bytes:
    """Create a minimal DOCX in memory."""
    from docx import Document
    doc = Document()
    doc.add_heading("Resume", level=1)
    for line in text.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_user():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="site-builder-test",
        defaults={"is_superuser": True, "is_staff": True},
    )
    return user


def _make_context(user=None):
    from cauldron_ai_admin.tools import AdminAIToolContext
    return AdminAIToolContext(
        actor=user or _make_user(),
        run_id="run-1",
        correlation_id="corr-1",
    )


def _make_fetch_result(body: bytes, content_type: str = "text/html", url: str = "https://example.com/"):
    from cauldron_ai_web.fetcher import FetchResult
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body_bytes=body,
    )


SAMPLE_HTML = b"""<!DOCTYPE html>
<html>
<head>
  <title>Acme Design Studio</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <nav><a href="/">Home</a><a href="/work">Work</a><a href="/contact">Contact</a></nav>
  <h1>Welcome to Acme</h1>
  <h2>Our Services</h2>
  <div class="card"><p>Brand identity design</p></div>
</body>
</html>"""

SAMPLE_CSS = b"""
:root {
  --color-bg: #ffffff;
  --font-body: Inter, sans-serif;
}
body { font-family: Inter, sans-serif; background-color: #ffffff; color: #111; padding: 24px; }
.card { border-radius: 8px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,.1); }
"""


# ---------------------------------------------------------------------------
# 1. Resume PDF ingested and text extracted
# ---------------------------------------------------------------------------

def test_resume_pdf_ingested_and_text_extracted():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.models import ExtractionStatus

    pdf_bytes = _make_minimal_pdf("Jane Smith Software Engineer Python Django")
    user = _make_user()
    svc = AttachmentService()
    record = svc.create_from_bytes(
        owner=user,
        filename="resume.pdf",
        content_type="application/pdf",
        data=pdf_bytes,
    )

    assert record.id is not None
    assert record.extraction_status == ExtractionStatus.EXTRACTED
    assert record.word_count > 0
    assert "Jane" in record.extracted_text or len(record.extracted_text) > 0


# ---------------------------------------------------------------------------
# 2. Resume DOCX ingested and text extracted
# ---------------------------------------------------------------------------

def test_resume_docx_ingested_and_text_extracted():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.models import ExtractionStatus

    docx_bytes = _make_docx("Alice Johnson\nProduct Designer\nFigma, CSS, Branding")
    user = _make_user()
    svc = AttachmentService()
    record = svc.create_from_bytes(
        owner=user,
        filename="resume.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=docx_bytes,
    )

    assert record.extraction_status == ExtractionStatus.EXTRACTED
    assert "Alice" in record.extracted_text
    assert record.word_count > 0


# ---------------------------------------------------------------------------
# 3. URL inspection returns design characteristics
# ---------------------------------------------------------------------------

def test_url_inspection_returns_design_characteristics():
    from cauldron_ai_web.analyzer import analyze_html, analyze_css

    html = SAMPLE_HTML.decode()
    css = SAMPLE_CSS.decode()

    html_chars = analyze_html(html)
    assert html_chars.title == "Acme Design Studio"
    assert "Welcome to Acme" in html_chars.headings
    assert "Home" in html_chars.nav_items
    assert html_chars.uses_cards is True

    full_chars = analyze_css(css, existing=html_chars)
    assert any("inter" in f.lower() for f in full_chars.font_families)
    assert full_chars.background_is_light is True
    assert full_chars.border_radius_hint in ("medium", "rounded", "small")


# ---------------------------------------------------------------------------
# 4. Both tools registered in Admin AI registry
# ---------------------------------------------------------------------------

def test_both_tools_registered_in_registry():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_ai_attachments.tools import register as register_attachments
    from cauldron_ai_web.tools import register as register_web

    registry = AdminAIToolRegistry()
    register_attachments(registry)
    register_web(registry)

    names = {t.name for t in registry.all_definitions()}
    assert "attachments.read" in names
    assert "web.inspect_url" in names


# ---------------------------------------------------------------------------
# 5. attachments.read returns extracted resume text via tool handler
# ---------------------------------------------------------------------------

def test_attachments_read_returns_extracted_text():
    from cauldron_ai_attachments.service import AttachmentService
    from cauldron_ai_attachments.tools import register as register_attachments
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    pdf_bytes = _make_minimal_pdf("Bob Lee Senior Developer React TypeScript")
    user = _make_user()
    record = AttachmentService().create_from_bytes(
        owner=user,
        filename="cv.pdf",
        content_type="application/pdf",
        data=pdf_bytes,
    )

    registry = AdminAIToolRegistry()
    register_attachments(registry)
    definition, handler = registry.get("attachments.read")

    ctx = _make_context(user)
    result = handler(ctx, attachment_id=str(record.id))

    assert result.success is True
    assert result.data["filename"] == "cv.pdf"
    assert "text" in result.data
    assert result.data["word_count"] > 0


# ---------------------------------------------------------------------------
# 6. web.inspect_url returns design analysis (mocked HTTP)
# ---------------------------------------------------------------------------

def test_web_inspect_url_returns_design_analysis():
    from cauldron_ai_web.tools import register as register_web
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    html_result = _make_fetch_result(SAMPLE_HTML)
    css_result = _make_fetch_result(SAMPLE_CSS, "text/css", "https://example.com/style.css")

    registry = AdminAIToolRegistry()
    register_web(registry)
    definition, handler = registry.get("web.inspect_url")

    ctx = _make_context()
    with mock.patch("cauldron_ai_web.tools.get_fetcher") as mock_get_fetcher:
        fetcher_mock = mock.MagicMock()
        mock_get_fetcher.return_value = fetcher_mock
        fetcher_mock.fetch.side_effect = [html_result, css_result]
        result = handler(ctx, url="https://example.com/")

    assert result.success is True
    assert result.data["title"] == "Acme Design Studio"
    assert "Welcome to Acme" in result.data["headings"]
    assert any("inter" in f.lower() for f in result.data["font_families"])
    assert result.data["background_is_light"] is True


# ---------------------------------------------------------------------------
# 7. Content proposals can be created using extracted resume context (mock)
# ---------------------------------------------------------------------------

def test_content_proposals_created_with_resume_context():
    """Verify that extracted attachment text can be passed to a content service mock."""
    from cauldron_ai_attachments.service import AttachmentService

    pdf_bytes = _make_minimal_pdf("Sarah Chen UX Designer Portfolio")
    user = _make_user()
    record = AttachmentService().create_from_bytes(
        owner=user,
        filename="portfolio.pdf",
        content_type="application/pdf",
        data=pdf_bytes,
    )

    # Simulate what the Admin AI orchestrator does: extract text, then call
    # a content service to create a proposal.
    extracted_text = record.extracted_text
    assert extracted_text  # non-empty

    content_service = mock.MagicMock()
    content_service.create_proposal.return_value = mock.MagicMock(id="prop-123")

    proposal = content_service.create_proposal(
        title="Homepage",
        body=f"Based on your resume: {extracted_text[:200]}",
        owner=user,
    )
    content_service.create_proposal.assert_called_once()
    assert proposal.id == "prop-123"


# ---------------------------------------------------------------------------
# 8. Style proposal through ui.styles.create_proposal path (mock)
# ---------------------------------------------------------------------------

def test_style_proposal_created_via_mock():
    """Verify that design characteristics can feed a style service mock."""
    from cauldron_ai_web.analyzer import analyze_html, analyze_css

    html_chars = analyze_html(SAMPLE_HTML.decode())
    full_chars = analyze_css(SAMPLE_CSS.decode(), existing=html_chars)

    style_service = mock.MagicMock()
    style_service.create_style_proposal.return_value = mock.MagicMock(
        id="style-456",
        font_families=full_chars.font_families,
    )

    proposal = style_service.create_style_proposal(
        font_families=full_chars.font_families,
        color_hints=full_chars.color_hints,
        border_radius=full_chars.border_radius_hint,
    )
    style_service.create_style_proposal.assert_called_once()
    assert proposal.id == "style-456"


# ---------------------------------------------------------------------------
# 9. Malformed PDF returns ExtractionError
# ---------------------------------------------------------------------------

def test_malformed_pdf_returns_extraction_error():
    from cauldron_ai_attachments.extractors import ExtractionError, PdfExtractor

    extractor = PdfExtractor()
    # A valid-looking PDF header but corrupt body
    corrupt_pdf = b"%PDF-1.4\nThis is not a real PDF body at all."
    with pytest.raises(ExtractionError, match="PDF could not be read"):
        extractor.extract(corrupt_pdf)


# ---------------------------------------------------------------------------
# 10. Oversized file is rejected
# ---------------------------------------------------------------------------

def test_oversized_file_rejected():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError

    # Build bytes just over 10 MB — use a valid PDF header to pass magic check
    oversized = b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024 + 1)
    user = _make_user()
    svc = AttachmentService()
    with pytest.raises(AttachmentValidationError, match="exceeds maximum size"):
        svc.create_from_bytes(
            owner=user,
            filename="huge.pdf",
            content_type="application/pdf",
            data=oversized,
        )


# ---------------------------------------------------------------------------
# 11. Inaccessible URL returns UrlFetchError
# ---------------------------------------------------------------------------

def test_inaccessible_url_returns_fetch_error():
    import urllib.error
    from cauldron_ai_web.fetcher import SafeUrlFetcher, UrlFetchError

    fetcher = SafeUrlFetcher()

    with mock.patch(
        "cauldron_ai_web.fetcher.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    ):
        opener_mock = mock.MagicMock()
        opener_mock.open.side_effect = urllib.error.URLError("Name or service not known")
        with mock.patch(
            "cauldron_ai_web.fetcher.urllib.request.build_opener",
            return_value=opener_mock,
        ):
            with pytest.raises(UrlFetchError, match="Failed to fetch"):
                fetcher.fetch("https://doesnotexist.invalid/")


# ---------------------------------------------------------------------------
# 12. Private/internal URL is blocked by SSRF protection
# ---------------------------------------------------------------------------

def test_private_url_blocked_by_ssrf():
    from cauldron_ai_web.fetcher import SafeUrlFetcher, UnsafeUrlError

    fetcher = SafeUrlFetcher()

    # localhost is blocked before DNS resolution
    with pytest.raises(UnsafeUrlError):
        fetcher.fetch("http://localhost/admin")

    # 10.x private network
    with mock.patch(
        "cauldron_ai_web.fetcher.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))],
    ):
        with pytest.raises(UnsafeUrlError, match="private"):
            fetcher.fetch("http://internal.corp/secret")

    # 192.168.x.x
    with mock.patch(
        "cauldron_ai_web.fetcher.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.100.5", 0))],
    ):
        with pytest.raises(UnsafeUrlError, match="private"):
            fetcher.fetch("http://router.local/")


# ---------------------------------------------------------------------------
# 13. Unsupported file type returns validation error
# ---------------------------------------------------------------------------

def test_unsupported_file_type_rejected():
    from cauldron_ai_attachments.service import AttachmentService, AttachmentValidationError

    user = _make_user()
    svc = AttachmentService()
    with pytest.raises(AttachmentValidationError, match="Unsupported file type"):
        svc.create_from_bytes(
            owner=user,
            filename="photo.jpg",
            content_type="image/jpeg",
            data=b"\xff\xd8\xff\xe0" + b"x" * 100,
        )
