"""Read-side queries. No side effects, no caching decisions."""

from .models import Craft, State


def active_crafts():
    return Craft.objects.filter(is_active=True).prefetch_related("talent_types")


def active_states():
    return State.objects.filter(is_active=True).prefetch_related("cities")


def serialize_crafts() -> list[dict]:
    return [
        {
            "id": str(craft.id),
            "slug": craft.slug,
            "title": craft.title,
            "description": craft.description,
            "talent_types": [
                {"id": str(t.id), "slug": t.slug, "title": t.title}
                for t in craft.talent_types.all()
                if t.is_active
            ],
        }
        for craft in active_crafts()
    ]


def serialize_locations() -> list[dict]:
    return [
        {
            "id": str(state.id),
            "slug": state.slug,
            "name": state.name,
            "cities": [
                {"id": str(c.id), "slug": c.slug, "name": c.name}
                for c in state.cities.all()
                if c.is_active
            ],
        }
        for state in active_states()
    ]
