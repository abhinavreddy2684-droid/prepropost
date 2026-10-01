import factory

from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile


class RecruiterProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RecruiterProfile

    user = factory.SubFactory(UserFactory)
    display_name = factory.Sequence(lambda n: f"Recruiter {n}")
    verification_status = RecruiterProfile.Verification.APPROVED
