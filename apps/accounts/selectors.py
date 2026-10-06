from uuid import UUID

from .models import User


def email_in_use(email: str) -> bool:
    return User.objects.filter(email__iexact=email.strip()).exists()


def get_user(user_id: UUID | str) -> User | None:
    return User.objects.filter(pk=user_id).first()
