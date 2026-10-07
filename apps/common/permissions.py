from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """Signed in as platform staff (`User.is_staff`)."""

    message = "Staff access is required."
    code = "staff_required"

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)
