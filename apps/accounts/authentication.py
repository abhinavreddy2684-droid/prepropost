"""Authentication for endpoints that are public but behave differently for signed-in callers."""

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class OptionalJWTAuthentication(JWTAuthentication):
    """Reads a valid bearer token; a missing, expired, malformed or revoked one is anonymous.

    Plain `JWTAuthentication` answers 401 to a stale token even on a public endpoint, which would
    break browsing the moment an access token lapses. Use this only on views whose permission is
    `AllowAny`, and let the view decide what a signed-in caller may additionally do.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except AuthenticationFailed:  # includes InvalidToken, and an inactive or unknown user
            return None
