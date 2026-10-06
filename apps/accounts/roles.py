"""Roles are implied by optional profiles, so apps contribute them here.

`accounts` sits below `talent` and `recruiters` in the dependency order, so it cannot import
them. Each app registers a predicate in `AppConfig.ready()` (like `reference.registry`), and
`/api/me` reports whichever roles hold *now*. Roles are not put in JWT claims: profiles are
created after login and a claim would go stale.
"""

from collections.abc import Callable

from .models import User

_roles: dict[str, Callable[[User], bool]] = {}


def register_role(name: str, predicate: Callable[[User], bool]) -> None:
    existing = _roles.get(name)
    if existing is not None and existing is not predicate:
        raise ValueError(f"Role {name!r} is already registered.")
    _roles[name] = predicate


def roles_for(user: User) -> list[str]:
    return sorted(name for name, holds in _roles.items() if holds(user))
