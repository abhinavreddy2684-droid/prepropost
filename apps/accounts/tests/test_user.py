import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_email_is_normalised_and_password_hashed():
    user = User.objects.create_user("  Mixed@Example.COM ", "secret-123")
    assert user.email == "mixed@example.com"
    assert user.password != "secret-123"
    assert user.check_password("secret-123")


def test_duplicate_email_rejected_regardless_of_case():
    User.objects.create_user("a@example.com", "x")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("A@EXAMPLE.COM", "x")


def test_suspended_user_is_inactive():
    user = User.objects.create_user("s@example.com", "x", status=User.Status.SUSPENDED)
    assert user.is_active is False


def test_superuser_flags():
    admin = User.objects.create_superuser("root@example.com", "x")
    assert admin.is_staff and admin.is_superuser
