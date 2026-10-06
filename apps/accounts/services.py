"""Account write use cases: registration, login and email verification."""

import contextlib
from datetime import datetime

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.events import publish
from apps.common.exceptions import Conflict, Unauthenticated, ValidationFailed

from . import selectors
from .events import EmailVerificationRequested, EmailVerified, UserRegistered
from .models import User

_VERIFY_SALT = "accounts.email-verification"


def _normalize_email(email: str) -> str:
    return User.objects.normalize_email(email)


@transaction.atomic
def register_user(*, email: str, password: str) -> User:
    email = _normalize_email(email)
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationFailed(
            "Enter a valid email address.", details={"email": exc.messages}
        ) from exc
    try:
        validate_password(password, user=User(email=email))
    except ValidationError as exc:
        raise ValidationFailed("Password is too weak.", details={"password": exc.messages}) from exc
    if selectors.email_in_use(email):
        raise Conflict("An account with this email already exists.", code="email_taken")
    try:
        with transaction.atomic():
            user = User.objects.create_user(email, password)
    except IntegrityError as exc:  # lost a race with a concurrent registration
        raise Conflict("An account with this email already exists.", code="email_taken") from exc
    publish(UserRegistered(user_id=user.pk))
    publish(EmailVerificationRequested(user_id=user.pk))
    return user


@transaction.atomic
def login_user(*, email: str, password: str) -> User:
    """Verify credentials. One generic error for unknown email, wrong password or suspension."""
    user = authenticate(email=_normalize_email(email), password=password)
    if user is None:
        raise Unauthenticated("Invalid email or password.")
    update_last_login(None, user)
    return user


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


@transaction.atomic
def logout(*, refresh_token: str) -> None:
    """Revoke a refresh token. Idempotent: an invalid or already revoked token is a no-op."""
    with contextlib.suppress(TokenError):
        RefreshToken(refresh_token).blacklist()


def build_verification_token(user: User) -> str:
    """Signed, expiring, and bound to the current email so a changed address invalidates it."""
    return signing.dumps({"uid": str(user.pk), "email": user.email}, salt=_VERIFY_SALT)


def build_verification_url(user: User) -> str:
    return f"{settings.FRONTEND_URL}/verify-email?token={build_verification_token(user)}"


@transaction.atomic
def request_email_verification(*, user: User) -> None:
    if user.is_email_verified:
        raise Conflict("Email is already verified.", code="already_verified")
    publish(EmailVerificationRequested(user_id=user.pk))


@transaction.atomic
def verify_email(*, token: str, now: datetime | None = None) -> User:
    max_age = settings.EMAIL_VERIFICATION_TTL_HOURS * 3600
    try:
        payload = signing.loads(token, salt=_VERIFY_SALT, max_age=max_age)
    except signing.SignatureExpired as exc:
        raise ValidationFailed("This verification link has expired.", code="token_expired") from exc
    except signing.BadSignature as exc:
        raise ValidationFailed("This verification link is invalid.", code="invalid_token") from exc

    user = User.objects.select_for_update().filter(pk=payload.get("uid")).first()
    if user is None or user.email != payload.get("email"):
        raise ValidationFailed("This verification link is invalid.", code="invalid_token")
    if user.is_email_verified:
        return user
    user.email_verified_at = now or timezone.now()
    user.save(update_fields=["email_verified_at", "updated_at"])
    publish(EmailVerified(user_id=user.pk))
    return user
