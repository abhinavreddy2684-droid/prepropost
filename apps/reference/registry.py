"""Lets any app contribute enum choices to the bootstrap payload.

This keeps `reference` independent: it never imports talent, media, hiring ...
Those apps register their own TextChoices in AppConfig.ready().
"""

from django.db.models import TextChoices

_enums: dict[str, type[TextChoices]] = {}


def register_enum(name: str, choices: type[TextChoices]) -> None:
    existing = _enums.get(name)
    if existing is not None and existing is not choices:
        raise ValueError(f"Enum '{name}' is already registered.")
    _enums[name] = choices


def serialized_enums() -> dict[str, list[dict[str, str]]]:
    return {
        name: [{"value": value, "label": str(label)} for value, label in choices.choices]
        for name, choices in sorted(_enums.items())
    }
