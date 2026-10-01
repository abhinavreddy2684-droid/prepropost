"""Write-side operations and the bootstrap cache."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils.text import slugify

from . import selectors
from .models import City, Craft, State, TalentType
from .registry import serialized_enums

_CACHE_PREFIX = "reference:bootstrap:"


@dataclass(frozen=True)
class BootstrapSnapshot:
    payload: dict
    version: str

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


def _enum_signature() -> str:
    raw = json.dumps(serialized_enums(), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _cache_key() -> str:
    # Enum registry is part of the key, so deploying a new enum never serves a stale payload.
    return f"{_CACHE_PREFIX}{_enum_signature()}"


def _build_snapshot() -> BootstrapSnapshot:
    body = {
        "crafts": selectors.serialize_crafts(),
        "locations": selectors.serialize_locations(),
        "enums": serialized_enums(),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    version = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return BootstrapSnapshot(payload={"version": version, **body}, version=version)


def get_bootstrap() -> BootstrapSnapshot:
    key = _cache_key()
    snapshot = cache.get(key)
    if snapshot is None:
        snapshot = _build_snapshot()
        cache.set(key, snapshot, settings.REFERENCE_CACHE_TTL_SECONDS)
    return snapshot


def invalidate_bootstrap_cache() -> None:
    """Deferred to commit so a concurrent reader cannot re-cache pre-commit data."""
    transaction.on_commit(lambda: cache.delete(_cache_key()))


# ------------------------------------------------------------------ seeding
@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0


def _upsert(model, lookup: dict, defaults: dict, result: SyncResult):
    obj, created = model.objects.update_or_create(**lookup, defaults=defaults)
    if created:
        result.created += 1
    else:
        result.updated += 1
    return obj


@transaction.atomic
def sync_crafts(items: Iterable[Mapping]) -> SyncResult:
    """Idempotent: safe to run on every deploy."""
    result = SyncResult()
    for order, item in enumerate(items, start=1):
        craft = _upsert(
            Craft,
            {"slug": slugify(item["title"])},
            {
                "title": item["title"],
                "description": item.get("description", ""),
                "sort_order": item.get("sort_order", order),
                "is_active": True,
            },
            result,
        )
        for type_order, title in enumerate(item.get("talent_types", []), start=1):
            _upsert(
                TalentType,
                {"craft": craft, "slug": slugify(title)},
                {"title": title, "sort_order": type_order, "is_active": True},
                result,
            )
    return result


@transaction.atomic
def sync_locations(items: Iterable[Mapping]) -> SyncResult:
    result = SyncResult()
    for order, item in enumerate(items, start=1):
        state = _upsert(
            State,
            {"slug": slugify(item["state"])},
            {"name": item["state"], "sort_order": order, "is_active": True},
            result,
        )
        for city_order, name in enumerate(item.get("cities", []), start=1):
            _upsert(
                City,
                {"state": state, "slug": slugify(name)},
                {"name": name, "sort_order": city_order, "is_active": True},
                result,
            )
    return result
