import pytest
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts import tasks

from .factories import UserFactory

pytestmark = pytest.mark.django_db


def test_refresh_rotation_blacklists_the_old_token():
    user = UserFactory()
    old = RefreshToken.for_user(user)

    data = TokenRefreshSerializer(data={"refresh": str(old)})
    assert data.is_valid(), data.errors
    assert data.validated_data["refresh"] != str(old)

    assert BlacklistedToken.objects.filter(token__jti=old["jti"]).exists()
    with pytest.raises(TokenError):
        RefreshToken(str(old))


def test_access_token_carries_the_uuid_user_id():
    user = UserFactory()
    token = RefreshToken.for_user(user).access_token
    assert token["user_id"] == str(user.pk)


def test_suspended_user_is_rejected_by_jwt_authentication():
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import AuthenticationFailed

    user = UserFactory(status="suspended")
    token = str(RefreshToken.for_user(user).access_token)
    with pytest.raises(AuthenticationFailed):
        JWTAuthentication().get_user(JWTAuthentication().get_validated_token(token.encode()))


def test_bootstrap_stays_public_with_jwt_as_the_default_authentication():
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]
    assert APIClient().get("/api/bootstrap").status_code == 200


def test_flush_expired_tokens_task_runs():
    user = UserFactory()
    RefreshToken.for_user(user)
    tasks.flush_expired_tokens()
    assert OutstandingToken.objects.filter(user=user).exists()  # not expired, so kept
