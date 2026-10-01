import factory
from django.utils.text import slugify

from apps.reference.models import City, Craft, State


class CraftFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Craft

    title = factory.Sequence(lambda n: f"Craft {n}")
    slug = factory.LazyAttribute(lambda o: slugify(o.title))


class StateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = State

    name = factory.Sequence(lambda n: f"State {n}")
    slug = factory.LazyAttribute(lambda o: slugify(o.name))


class CityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = City

    state = factory.SubFactory(StateFactory)
    name = factory.Sequence(lambda n: f"City {n}")
    slug = factory.LazyAttribute(lambda o: slugify(o.name))
