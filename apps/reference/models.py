from django.db import models

from apps.common.models import ReferenceModel


class Craft(ReferenceModel):
    title = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    class Meta(ReferenceModel.Meta):
        constraints = [models.UniqueConstraint(fields=["slug"], name="craft_slug_unique")]

    def __str__(self) -> str:
        return self.title


class TalentType(ReferenceModel):
    """A specialisation inside a craft, e.g. Direction -> Assistant Directors."""

    craft = models.ForeignKey(Craft, on_delete=models.CASCADE, related_name="talent_types")
    title = models.CharField(max_length=120)

    class Meta(ReferenceModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["craft", "slug"], name="talenttype_craft_slug_unique")
        ]

    def __str__(self) -> str:
        return f"{self.craft.title} / {self.title}"


class State(ReferenceModel):
    name = models.CharField(max_length=120, unique=True)

    class Meta(ReferenceModel.Meta):
        constraints = [models.UniqueConstraint(fields=["slug"], name="state_slug_unique")]

    def __str__(self) -> str:
        return self.name


class City(ReferenceModel):
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name="cities")
    name = models.CharField(max_length=120)

    class Meta(ReferenceModel.Meta):
        verbose_name_plural = "cities"
        constraints = [
            models.UniqueConstraint(fields=["state", "slug"], name="city_state_slug_unique")
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.state.name}"
