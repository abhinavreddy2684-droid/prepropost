"""Shared hiring fixtures: verified recruiter, published talent, draft and sent offers."""

import pytest

from apps.hiring import services
from apps.reference.tests.factories import CraftFactory
from apps.talent.tests.factories import talent_with_craft

from .factories import ProjectFactory


@pytest.fixture
def craft():
    return CraftFactory(slug="playback-singing")


@pytest.fixture
def talent(craft):
    return talent_with_craft(craft)


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def recruiter(project):
    return project.recruiter


@pytest.fixture
def draft(recruiter, project, talent, craft):
    return services.create_offer(
        actor=recruiter,
        project=project,
        talent=talent,
        craft=craft,
        amount_minor=500_000,
        deliverables="Two songs, studio recorded",
    )


@pytest.fixture
def sent(draft, recruiter):
    return services.send_offer(actor=recruiter, offer_id=draft.id)
