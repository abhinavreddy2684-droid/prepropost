from collections.abc import Iterable

from .exceptions import ValidationFailed


def normalize_tags(
    values: Iterable[str] | None, *, max_items: int = 20, max_length: int = 40
) -> list[str]:
    """Lowercase, collapse whitespace, drop blanks and duplicates, keep first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values or []:
        tag = " ".join(str(raw).split()).lower()
        if not tag or tag in seen:
            continue
        if len(tag) > max_length:
            raise ValidationFailed(f"Tag '{tag[:20]}...' exceeds {max_length} characters.")
        seen.add(tag)
        result.append(tag)
    if len(result) > max_items:
        raise ValidationFailed(f"At most {max_items} tags are allowed.")
    return result
