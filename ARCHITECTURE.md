# Pre Pro Post: backend architecture

A modular monolith. Each app is a bounded context with a small public surface;
nothing reaches into another app's tables or internals.

## Layers inside every app

| File | Responsibility | May call |
|---|---|---|
| `models.py` | Schema, DB constraints, enums. No business logic. | - |
| `selectors.py` | Read-side queries. No side effects. | models |
| `services.py` | Write-side use cases. Atomic, validated, authoritative. | models, selectors, other apps' **selectors/services** |
| `events.py` | Public domain events (plain ids only). | - |
| `subscribers.py` | Reactions to other apps' events. | own services |
| `views/serializers` (M1.1+) | HTTP translation only. Calls services/selectors. | services, selectors |
| `admin.py` | Staff tooling; mutates via services where state matters. | services |

Rule of thumb: **views never contain business rules; services never know about HTTP.**

## Dependency direction (no cycles)

```
common  <-  accounts, reference  <-  media_library, talent, recruiters  <-  hiring
                                                                              ^
                                    notifications, analytics  ---(subscribe)--+
```

* `hiring` asks `recruiters.selectors.is_verified_recruiter` and
  `talent.selectors.has_craft` - it never queries their tables.
* `notifications` and `analytics` subscribe to `hiring.events`; `hiring` does not
  know they exist. Add a new reaction (email, CRM sync) without touching hiring.
* `reference` never imports other apps: they register their enums via
  `reference.registry.register_enum(...)` in `AppConfig.ready()`.

## Cross-cutting building blocks (`apps.common`)

* `models`: `BaseModel` (UUID + timestamps), `SoftDeleteModel`, `ReferenceModel`.
* `exceptions`: `DomainError` family; mapped to HTTP once in `api.exception_handler`
  (`{"error": {"code", "message", "details"}}`).
* `state_machine.StateMachine`: declarative transition tables, reused by offers
  and recruiter verification (and engagements in M5).
* `events`: typed in-process bus; dispatches after commit; subscriber failures are
  isolated and logged.

## Integrity is enforced in the database, not just in Python

One primary craft per talent, one live offer per (project, talent, craft),
positive amounts, ordered dates, ready media has a storage key, avatars are images.
Services give friendly errors; constraints guarantee correctness under concurrency.

## Extension points

| Need | Where |
|---|---|
| New craft / city / state | Admin or `seed_reference`. No code change; bootstrap cache refreshes itself. |
| New enum for the frontend | `register_enum("name", Choices)` in the owning app. |
| New talent filter | One field on `TalentFilters` + one clause in `search_talents`. |
| Craft-specific fields (vocal range ...) | `TalentCraft.attributes` JSON (validate per craft later). |
| New offer transition | One row in `hiring/state_machine.py` + a service + an event. |
| New reaction to offers | New `@subscribe(...)` handler in any app. |

## Configuration

Environment via `django-environ`; settings split `base/local/test/production`.
Tunables (`AVAILABILITY_WINDOW_DAYS`, `OFFER_DEFAULT_TTL_DAYS`, cache TTLs,
`PLATFORM_COMMISSION_BPS`) live in `base.py`, not in code.

## Conscious limitations (to revisit)

* Event bus is in-process; move `_dispatch` onto Celery when volume or reliability needs it
  (add an outbox table if you need guaranteed delivery).
* No auth/serializer layer yet (milestone M1.1). `ATOMIC_REQUESTS` is on; domain errors
  are returned as responses, so services must remain internally atomic (they are).
* Engagement, ledger, payouts and disputes (M5/M6) are not part of this drop.
