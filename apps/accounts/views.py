from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.common.serializers import ErrorEnvelopeSerializer

from . import serializers, services
from .roles import roles_for

_ERROR = OpenApiResponse(ErrorEnvelopeSerializer)


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
        request=serializers.RegisterSerializer,
        responses={201: serializers.RegisteredSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.register_user(**data.validated_data)
        return Response(
            {**services.issue_tokens(user), "user": _me(user)}, status=status.HTTP_201_CREATED
        )


class LoginView(_PublicView):
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Exchange email and password for tokens",
        request=serializers.LoginSerializer,
        responses={200: serializers.TokenPairSerializer, 401: _ERROR},
    )
    def post(self, request):
        data = serializers.LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.login_user(**data.validated_data)
        return Response(services.issue_tokens(user))


class RefreshView(TokenRefreshView):
    """Rotates the refresh token; the old one is blacklisted."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"


class LogoutView(_PublicView):
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Revoke a refresh token",
        request=serializers.RefreshTokenSerializer,
        responses={204: None, 400: _ERROR},
    )
    def post(self, request):
        data = serializers.RefreshTokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.logout(refresh_token=data.validated_data["refresh"])
        return Response(status=status.HTTP_204_NO_CONTENT)


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
