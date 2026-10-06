import factory

from apps.accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    password = "pass-12345"

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # Go through the manager so the password is hashed and stored.
        return model_class.objects.create_user(
            kwargs.pop("email"), kwargs.pop("password"), **kwargs
        )
