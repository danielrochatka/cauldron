"""Standard API response envelopes."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def success_response(data: Any, meta: dict | None = None, status: int = 200) -> JsonResponse:
    return JsonResponse({"data": data, "meta": meta or {}}, status=status)


def error_response(code: str, message: str, details: list | None = None, status: int = 400) -> JsonResponse:
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
            }
        },
        status=status,
    )


def unexpected_error_response(exc: Exception, correlation_id: str = "") -> JsonResponse:
    logger.error("Unexpected API error [%s]: %s", correlation_id, exc, exc_info=True)
    return JsonResponse(
        {
            "error": {
                "code": "internal.unexpected_error",
                "message": "An unexpected error occurred.",
                "details": [f"correlation_id: {correlation_id}"] if correlation_id else [],
            }
        },
        status=500,
    )
