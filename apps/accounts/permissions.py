from rest_framework.permissions import BasePermission


class IsEmailVerified(BasePermission):
    """Signed in with a confirmed email address."""

    message = "Verify your email address to do this."
    code = "email_not_verified"

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_email_verified)
