from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.common.serializers import ErrorEnvelopeSerializer

from . import cookies, serializers, services
from .roles import roles_for

_ERROR = OpenApiResponse(ErrorEnvelopeSerializer)
_SET_COOKIE = OpenApiParameter(
    name="Set-Cookie",
    type=str,
    location=OpenApiParameter.HEADER,
    response=[200, 201],
    description=(
        "The refresh token as an HttpOnly cookie (name AUTH_REFRESH_COOKIE_NAME, default "
        "`refresh_token`; Path=/api/auth/; SameSite=Lax; Secure in production; max-age = refresh "
        "lifetime). It is never in the response body."
    ),
)


def _with_refresh_cookie(response: Response, tokens: dict) -> Response:
    """Split an issued pair: the access token stays in the body, the refresh goes in the cookie."""
    cookies.set_refresh_cookie(response, tokens["refresh"])
    return response


def _me(user) -> dict:
    return {
        "id": user.pk,
        "email": user.email,
        "email_verified": user.is_email_verified,
        "roles": roles_for(user),
    }


class _PublicView(APIView):
    """Credential endpoints: no token needed, tighter scoped throttle (set per subclass)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]


class RegisterView(_PublicView):
    throttle_scope = "auth_register"

    @extend_schema(
        summary="Create an account and sign in",
        description="Returns the access token and sets the refresh cookie.",
        parameters=[_SET_COOKIE],
        request=serializers.RegisterSerializer,
        responses={201: serializers.RegisteredSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.register_user(**data.validated_data)
        tokens = services.issue_tokens(user)
        response = Response(
            {"access": tokens["access"], "user": _me(user)}, status=status.HTTP_201_CREATED
        )
        return _with_refresh_cookie(response, tokens)


class LoginView(_PublicView):
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Exchange email and password for an access token",
        description="Returns the access token and sets the refresh cookie.",
        parameters=[_SET_COOKIE],
        request=serializers.LoginSerializer,
        responses={200: serializers.AccessTokenSerializer, 401: _ERROR},
    )
    def post(self, request):
        data = serializers.LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.login_user(**data.validated_data)
        tokens = services.issue_tokens(user)
        return _with_refresh_cookie(Response({"access": tokens["access"]}), tokens)


class RefreshView(_PublicView):
    """Reads the refresh cookie, never the body. The old token is blacklisted on rotation."""

    throttle_scope = "auth_login"

    @extend_schema(
        summary="Get a new access token from the refresh cookie",
        description=(
            "No body. Reads the refresh cookie, rotates it (the old token is blacklisted) and "
            "returns a new access token with a rotated cookie. A missing, invalid, expired or "
            "already used cookie is a 401; a refresh token in the body is ignored."
        ),
        parameters=[_SET_COOKIE],
        request=None,
        responses={200: serializers.AccessTokenSerializer, 401: _ERROR},
    )
    def post(self, request):
        tokens = services.refresh_tokens(refresh_token=cookies.read_refresh_cookie(request))
        return _with_refresh_cookie(Response({"access": tokens["access"]}), tokens)


class LogoutView(_PublicView):
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Sign out: revoke the refresh cookie's token and clear the cookie",
        description=(
            "No body. Idempotent: always 204, even with no cookie or an invalid one. Access "
            "tokens stay valid until they expire."
        ),
        request=None,
        responses={204: None},
    )
    def post(self, request):
        services.logout(refresh_token=cookies.read_refresh_cookie(request))
        return cookies.clear_refresh_cookie(Response(status=status.HTTP_204_NO_CONTENT))


class VerifyEmailView(_PublicView):
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Confirm an email address from the link in the verification email",
        request=serializers.VerifyEmailSerializer,
        responses={200: serializers.MeSerializer, 400: _ERROR},
    )
    def post(self, request):
        data = serializers.VerifyEmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.verify_email(token=data.validated_data["token"])
        return Response(_me(user))


class ResendVerificationView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email_resend"

    @extend_schema(
        summary="Send the verification email again",
        request=None,
        responses={204: None, 401: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        services.request_email_verification(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    @extend_schema(
        summary="The signed-in account and its roles",
        responses={200: serializers.MeSerializer, 401: _ERROR},
    )
    def get(self, request):
        return Response(_me(request.user))
