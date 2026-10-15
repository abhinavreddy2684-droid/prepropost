"""Domain error taxonomy.

Services raise these; they know nothing about HTTP. The API layer maps them to
status codes in one place (apps.common.api.exception_handler).
"""


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str = "", *, code: str | None = None, details: dict | None = None):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code:
            self.code = code
        self.details = details or {}


class ValidationFailed(DomainError):
    code = "validation_failed"


class Unauthenticated(DomainError):
    """Credentials were missing or wrong. Mapped to HTTP 401."""

    code = "authentication_failed"


class NotFound(DomainError):
    code = "not_found"


class PermissionDenied(DomainError):
    code = "permission_denied"


class Conflict(DomainError):
    code = "conflict"


class InvalidTransition(Conflict):
    code = "invalid_transition"


class Gone(DomainError):
    """The resource existed but can no longer be used (e.g. an expired upload). HTTP 410."""

    code = "gone"


class Unavailable(DomainError):
    """A dependency (object storage ...) is temporarily unreachable; retry later. HTTP 503."""

    code = "unavailable"
