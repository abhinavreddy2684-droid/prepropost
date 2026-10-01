import factory

from apps.hiring.models import Project
from apps.recruiters.tests.factories import RecruiterProfileFactory


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    recruiter = factory.LazyAttribute(lambda o: RecruiterProfileFactory().user)
    title = factory.Sequence(lambda n: f"Project {n}")
