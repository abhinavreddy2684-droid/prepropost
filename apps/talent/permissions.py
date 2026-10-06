from rest_framework.permissions import BasePermission

from .selectors import has_talent_profile


class IsTalent(BasePermission):
    """Signed in and holding a talent profile (roles come from profiles, not the token)."""

    message = "A talent profile is required."
    code = "talent_role_required"

    def has_permission(self, request, view) -> bool:
        return has_talent_profile(request.user)
