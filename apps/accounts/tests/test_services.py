import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts import events, services
from apps.accounts.models import User
from apps.common.exceptions import Conflict, Unauthenticated, ValidationFailed

from .factories import UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "a-Long-pass-12345"


def _token_from_outbox() -> str:
    return re.search(r"token=(\S+)", mail.outbox[-1].body).group(1)


class TestRegisterUser:
    def test_normalizes_email_and_hashes_password(self):
        user = services.register_user(email="  New.User@Example.COM ", password=PASSWORD)
        assert user.email == "new.user@example.com"
        assert user.check_password(PASSWORD)
        assert not user.is_email_verified

    def test_duplicate_email_is_rejected_regardless_of_case(self):
        UserFactory(email="taken@example.com")
        with pytest.raises(Conflict) as exc:
            services.register_user(email="TAKEN@example.com", password=PASSWORD)
        assert exc.value.code == "email_taken"

    @pytest.mark.parametrize("email", ["", "not-an-email", "a@b"])
    def test_invalid_email_is_rejected(self, email):
        with pytest.raises(ValidationFailed):
            services.register_user(email=email, password=PASSWORD)

    def test_weak_password_is_rejected_and_nothing_is_created(self):
        with pytest.raises(ValidationFailed) as exc:
            services.register_user(email="a@example.com", password="12345678")
        assert "password" in exc.value.details
        assert not User.objects.filter(email="a@example.com").exists()

    def test_publishes_events_and_sends_one_verification_email_after_commit(
        self, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            user = services.register_user(email="a@example.com", password=PASSWORD)
            assert mail.outbox == []  # nothing before commit
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["a@example.com"]
        assert services.verify_email(token=_token_from_outbox()).pk == user.pk

    def test_emits_registered_and_verification_requested(self):
        with mock.patch("apps.accounts.services.publish") as publish:
            user = services.register_user(email="a@example.com", password=PASSWORD)
        assert [type(c.args[0]) for c in publish.call_args_list] == [
            events.UserRegistered,
            events.EmailVerificationRequested,
        ]
        assert all(c.args[0].user_id == user.pk for c in publish.call_args_list)


class TestLoginUser:
    def test_succeeds_case_insensitively_and_records_last_login(self):
        user = UserFactory(email="login@example.com")
        assert user.last_login is None
        logged_in = services.login_user(email=" LOGIN@example.com", password="pass-12345")
        assert logged_in.pk == user.pk
        user.refresh_from_db()
        assert user.last_login is not None

    @pytest.mark.parametrize("email", ["login@example.com", "nobody@example.com"])
    def test_wrong_password_and_unknown_email_look_identical(self, email):
        UserFactory(email="login@example.com")
        with pytest.raises(Unauthenticated) as exc:
            services.login_user(email=email, password="wrong")
        assert exc.value.message == "Invalid email or password."

    def test_suspended_user_cannot_log_in(self):
        UserFactory(email="s@example.com", status=User.Status.SUSPENDED)
        with pytest.raises(Unauthenticated):
            services.login_user(email="s@example.com", password="pass-12345")


class TestVerifyEmail:
    def test_marks_verified_and_publishes(self):
        user = UserFactory()
        now = timezone.now()
        with mock.patch("apps.accounts.services.publish") as publish:
            services.verify_email(token=services.build_verification_token(user), now=now)
        user.refresh_from_db()
        assert user.email_verified_at == now
        assert publish.call_args.args[0] == events.EmailVerified(user_id=user.pk)

    def test_is_idempotent(self):
        user = UserFactory()
        token = services.build_verification_token(user)
        first = services.verify_email(token=token).email_verified_at
        with mock.patch("apps.accounts.services.publish") as publish:
            second = services.verify_email(token=token).email_verified_at
        assert first == second
        publish.assert_not_called()

    def test_tampered_token_is_invalid(self):
        token = services.build_verification_token(UserFactory())
        with pytest.raises(ValidationFailed) as exc:
            services.verify_email(token=token[:-2] + "xx")
        assert exc.value.code == "invalid_token"

    def test_expired_token_is_rejected(self, settings):
        user = UserFactory()
        token = services.build_verification_token(user)
        expiry = timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS + 1)
        with freeze_time(timezone.now() + expiry), pytest.raises(ValidationFailed) as exc:
            services.verify_email(token=token)
        assert exc.value.code == "token_expired"
        user.refresh_from_db()
        assert not user.is_email_verified

    def test_token_dies_when_the_email_changes(self):
        user = UserFactory()
        token = services.build_verification_token(user)
        user.email = "changed@example.com"
        user.save()
        with pytest.raises(ValidationFailed):
            services.verify_email(token=token)


class TestRequestEmailVerification:
    def test_resend_sends_another_email(self, django_capture_on_commit_callbacks):
        user = UserFactory()
        with django_capture_on_commit_callbacks(execute=True):
            services.request_email_verification(user=user)
        assert len(mail.outbox) == 1

    def test_already_verified_is_a_conflict(self):
        user = UserFactory(email_verified_at=timezone.now())
        with pytest.raises(Conflict) as exc:
            services.request_email_verification(user=user)
        assert exc.value.code == "already_verified"

    def test_no_email_is_sent_to_a_user_verified_in_the_meantime(
        self, django_capture_on_commit_callbacks
    ):
        user = UserFactory()
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            services.request_email_verification(user=user)
        services.verify_email(token=services.build_verification_token(user))
        for callback in callbacks:
            callback()
        assert mail.outbox == []
