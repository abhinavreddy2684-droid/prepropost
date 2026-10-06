import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def auth_client():
    """Factory returning (APIClient, user) with a valid access token attached."""
    from rest_framework.test import APIClient

    from apps.accounts import services
    from apps.accounts.tests.factories import UserFactory

    def make(user=None):
        user = user or UserFactory()
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {services.issue_tokens(user)['access']}")
        return api, user

    return make


@pytest.fixture
def isolated_registry():
    """Lets a test register enums without leaking them into other tests."""
    from apps.reference import registry

    saved = dict(registry._enums)
    yield registry
    registry._enums.clear()
    registry._enums.update(saved)
