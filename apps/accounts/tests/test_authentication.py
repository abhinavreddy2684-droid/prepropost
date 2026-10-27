from datetime import timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.authentication import OptionalJWTAuthentication
from apps.accounts.models import User

from .factories import UserFactory

pytestmark = pytest.mark.django_db


class WhoAmI(APIView):
    authentication_classes = [OptionalJWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"authenticated": request.user.is_authenticated})


def _call(token=None, *, header=None):
    kwargs = {}
    if header or token:
        kwargs["HTTP_AUTHORIZATION"] = header or f"Bearer {token}"
    return WhoAmI.as_view()(APIRequestFactory().get("/x", **kwargs))


def _access(user) -> str:
    return str(RefreshToken.for_user(user).access_token)


def test_valid_token_authenticates():
    response = _call(_access(UserFactory()))
    assert response.status_code == 200
    assert response.data == {"authenticated": True}


@pytest.mark.parametrize(
    "header", [None, "Bearer garbage", "Bearer ", "Basic abc", "Bearer a.b.c", "Token xyz"]
)
def test_missing_or_invalid_token_is_anonymous_never_401(header):
    response = _call(header=header)
    assert response.status_code == 200
    assert response.data == {"authenticated": False}


def test_expired_token_is_anonymous():
    token = _access(UserFactory())
    with freeze_time(timezone.now() + timedelta(days=2)):
        response = _call(token)
    assert response.status_code == 200
    assert response.data == {"authenticated": False}


def test_refresh_token_is_not_an_access_token():
    response = _call(str(RefreshToken.for_user(UserFactory())))
    assert response.data == {"authenticated": False}


def test_suspended_or_deleted_user_is_anonymous():
    suspended = UserFactory(status=User.Status.SUSPENDED)
    deleted = UserFactory()
    token = _access(deleted)
    deleted.delete()
    assert _call(_access(suspended)).data == {"authenticated": False}
    assert _call(token).data == {"authenticated": False}
