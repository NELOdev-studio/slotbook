from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    payload.update(extra)
    return payload


def exception_handler(exc: Exception, context: dict[str, Any]):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    data = response.data
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return response
    if isinstance(data, dict) and data.get("code") and data.get("message"):
        response.data = error_payload(str(data["code"]), str(data["message"]))
    elif isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        code = getattr(detail, "code", None) or (
            "authentication_required" if response.status_code == 401 else "request_error"
        )
        response.data = error_payload(str(code), str(detail))
    else:
        code = _first_error_code(data)
        response.data = error_payload(
            code or ("validation_error" if response.status_code == 400 else "request_error"),
            "Request could not be processed.",
            details=data,
        )
    return response


def _first_error_code(value: Any) -> str | None:
    if isinstance(value, dict):
        for item in value.values():
            found = _first_error_code(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _first_error_code(item)
            if found:
                return found
    else:
        code = getattr(value, "code", None)
        if code and code != "invalid":
            return str(code)
    return None


def validation_error_code(error: DjangoValidationError) -> str:
    if getattr(error, "code", None):
        return str(error.code)
    if getattr(error, "error_dict", None):
        for errors in error.error_dict.values():
            if errors and getattr(errors[0], "code", None):
                return str(errors[0].code)
    if getattr(error, "error_list", None):
        for item in error.error_list:
            if getattr(item, "code", None):
                return str(item.code)
    return "validation_error"


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
