"""Per-purpose media policies.

Core media knows how to move bytes; it does not know what an avatar or a gallery is. Each owning
app registers a `MediaPolicy` for its purposes in `AppConfig.ready()` (same pattern as
`reference.registry.register_enum`), with numbers read from settings:

    register_policy(MediaPolicy(purpose="talent.avatar", rules=(...), max_in_flight=2, ...))

`request_upload` refuses any purpose that is not registered.
"""

from dataclasses import dataclass

_policies: dict[str, "MediaPolicy"] = {}


@dataclass(frozen=True)
class TypeRule:
    """One allowed kind of file for a purpose: its content types and size limit."""

    kind: str  # a Media.Kind value
    content_types: frozenset[str]
    max_bytes: int


@dataclass(frozen=True)
class MediaPolicy:
    purpose: str
    rules: tuple[TypeRule, ...]
    max_in_flight: int  # uploads per user for this purpose not yet READY / FAILED / EXPIRED
    thumbnail_sizes: tuple[int, ...] = ()  # images only; WebP, longest edge in pixels
    strip_metadata: bool = True  # images only (non-image stripping is not built)

    def rule_for(self, content_type: str) -> TypeRule | None:
        content_type = (content_type or "").strip().lower()
        return next((r for r in self.rules if content_type in r.content_types), None)

    @property
    def content_types(self) -> list[str]:
        return sorted(t for rule in self.rules for t in rule.content_types)


def register_policy(policy: MediaPolicy) -> None:
    existing = _policies.get(policy.purpose)
    if existing is not None and existing != policy:
        raise ValueError(f"Media policy '{policy.purpose}' is already registered.")
    _policies[policy.purpose] = policy


def get_policy(purpose: str) -> MediaPolicy | None:
    return _policies.get(purpose)


def registered_purposes() -> list[str]:
    return sorted(_policies)
