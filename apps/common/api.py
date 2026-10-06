"""Shared API plumbing: one error envelope, one pagination default."""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .exceptions import (
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    Unauthenticated,
    ValidationFailed,
)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    ValidationFailed: status.HTTP_400_BAD_REQUEST,
    Unauthenticated: status.HTTP_401_UNAUTHORIZED,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,  # also covers InvalidTransition
}


def _status_for(exc: DomainError) -> int:
    for error_type, http_status in _STATUS_BY_ERROR.items():
        if isinstance(exc, error_type):
            return http_status
    return status.HTTP_400_BAD_REQUEST


def exception_handler(exc, context):
    """Every error leaves the API as {"error": {"code", "message", "details"}}."""
    if isinstance(exc, DomainError):
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthenticated) else None
        return Response(
            {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            status=_status_for(exc),
            headers=headers,
        )
    response = drf_exception_handler(exc, context)
    if response is not None:
        code = exc.get_codes() if isinstance(exc, APIException) else "error"
        response.data = {
            "error": {
                "code": code if isinstance(code, str) else "validation_failed",
                "message": "Request could not be processed.",
                "details": response.data,
            }
        }
    return response


class DefaultCursorPagination(CursorPagination):
    page_size = 24
    max_page_size = 100
    page_size_query_param = "limit"
    ordering = "-created_at"
