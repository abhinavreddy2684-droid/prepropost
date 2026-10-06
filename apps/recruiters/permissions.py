from rest_framework.permissions import BasePermission

from .selectors import has_recruiter_profile, is_verified_recruiter


class IsRecruiter(BasePermission):
    message = "A recruiter profile is required."
    code = "recruiter_role_required"

    def has_permission(self, request, view) -> bool:
        return has_recruiter_profile(request.user)


class IsVerifiedRecruiter(BasePermission):
    message = "Your recruiter account must be approved to do this."
    code = "recruiter_not_verified"

    def has_permission(self, request, view) -> bool:
        return is_verified_recruiter(request.user)
