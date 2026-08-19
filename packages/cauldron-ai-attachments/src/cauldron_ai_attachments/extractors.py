"""Document text extractors for PDF, DOCX, and plain text."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_MAX_CHARS_DEFAULT = 50_000
_MAX_PAGES_DEFAULT = 100


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    page_count: int = 0
    word_count: int = 0
    section_headings: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    truncated: bool = False
    extractor: str = ""


class ExtractionError(Exception):
    pass


@runtime_checkable
class DocumentExtractor(Protocol):
    def can_extract(self, content_type: str, filename: str) -> bool: ...
    def extract(self, data: bytes, *, max_chars: int = _MAX_CHARS_DEFAULT) -> ExtractionResult: ...


class PlainTextExtractor:
    SUPPORTED_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
    SUPPORTED_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".rst", ".csv"})

    def can_extract(self, content_type: str, filename: str) -> bool:
        import os
        ext = os.path.splitext(filename.lower())[1]
        return content_type in self.SUPPORTED_TYPES or ext in self.SUPPORTED_EXTENSIONS

    def extract(self, data: bytes, *, max_chars: int = _MAX_CHARS_DEFAULT) -> ExtractionResult:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:
            raise ExtractionError(f"Plain text decode failed: {exc}") from exc

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        lines = text.splitlines()
        headings = tuple(
            line.lstrip("#").strip()
            for line in lines
            if line.startswith("#") and len(line) < 200
        )[:20]
        words = len(text.split())
        return ExtractionResult(
            text=text,
            page_count=0,
            word_count=words,
            section_headings=headings,
            truncated=truncated,
            extractor="plain_text",
        )


class PdfExtractor:
    SUPPORTED_TYPES = frozenset({"application/pdf"})
    SUPPORTED_EXTENSIONS = frozenset({".pdf"})

    def can_extract(self, content_type: str, filename: str) -> bool:
        import os
        ext = os.path.splitext(filename.lower())[1]
        return content_type in self.SUPPORTED_TYPES or ext in self.SUPPORTED_EXTENSIONS

    def extract(self, data: bytes, *, max_chars: int = _MAX_CHARS_DEFAULT) -> ExtractionResult:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ExtractionError("pypdf is required for PDF extraction") from exc

        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as exc:
            raise ExtractionError(f"PDF could not be read: {exc}") from exc

        page_count = len(reader.pages)
        parts: list[str] = []
        total_chars = 0
        truncated = False

        for i, page in enumerate(reader.pages[:_MAX_PAGES_DEFAULT]):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""

            if total_chars + len(page_text) > max_chars:
                remaining = max_chars - total_chars
                parts.append(page_text[:remaining])
                truncated = True
                break

            parts.append(page_text)
            total_chars += len(page_text)

        text = "\n\n".join(p for p in parts if p)
        words = len(text.split())

        lines = text.splitlines()
        headings = tuple(
            line.strip()
            for line in lines
            if line.strip() and len(line.strip()) < 100 and not line.startswith(" ")
            and len(line.strip().split()) <= 8
        )[:20]

        return ExtractionResult(
            text=text,
            page_count=page_count,
            word_count=words,
            section_headings=headings,
            truncated=truncated or page_count > _MAX_PAGES_DEFAULT,
            extractor="pdf",
        )


class DocxExtractor:
    SUPPORTED_TYPES = frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    })
    SUPPORTED_EXTENSIONS = frozenset({".docx"})

    def can_extract(self, content_type: str, filename: str) -> bool:
        import os
        ext = os.path.splitext(filename.lower())[1]
        return content_type in self.SUPPORTED_TYPES or ext in self.SUPPORTED_EXTENSIONS

    def extract(self, data: bytes, *, max_chars: int = _MAX_CHARS_DEFAULT) -> ExtractionResult:
        try:
            from docx import Document
        except ImportError as exc:
            raise ExtractionError("python-docx is required for DOCX extraction") from exc

        try:
            doc = Document(io.BytesIO(data))
        except Exception as exc:
            raise ExtractionError(f"DOCX could not be read: {exc}") from exc

        parts: list[str] = []
        headings: list[str] = []
        total_chars = 0
        truncated = False

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            if para.style.name.startswith("Heading") and len(text) < 100:
                headings.append(text)

            if total_chars + len(text) > max_chars:
                remaining = max_chars - total_chars
                parts.append(text[:remaining])
                truncated = True
                break

            parts.append(text)
            total_chars += len(text)

        text = "\n".join(parts)
        words = len(text.split())

        return ExtractionResult(
            text=text,
            page_count=0,
            word_count=words,
            section_headings=tuple(headings[:20]),
            truncated=truncated,
            extractor="docx",
        )


_EXTRACTORS: list[DocumentExtractor] = [
    PdfExtractor(),
    DocxExtractor(),
    PlainTextExtractor(),
]


def get_extractor_for(content_type: str, filename: str) -> DocumentExtractor | None:
    for extractor in _EXTRACTORS:
        if extractor.can_extract(content_type, filename):
            return extractor
    return None


def extract_document(
    data: bytes,
    content_type: str,
    filename: str,
    *,
    max_chars: int = _MAX_CHARS_DEFAULT,
) -> ExtractionResult:
    extractor = get_extractor_for(content_type, filename)
    if extractor is None:
        raise ExtractionError(f"No extractor for content_type={content_type!r} filename={filename!r}")
    return extractor.extract(data, max_chars=max_chars)
