import factory

from apps.accounts.tests.factories import UserFactory
from apps.talent.models import TalentCraft, TalentProfile


class TalentProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TalentProfile

    user = factory.SubFactory(UserFactory)
    full_name = factory.Sequence(lambda n: f"Talent Person {n}")
    professional_name = factory.Sequence(lambda n: f"Talent {n}")
    is_published = True


def talent_with_craft(craft, *, primary=True, **kwargs) -> TalentProfile:
    talent = TalentProfileFactory(**kwargs)
    TalentCraft.objects.create(talent=talent, craft=craft, is_primary=primary)
    return talent
