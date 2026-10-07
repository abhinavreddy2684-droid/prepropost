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
| `views/serializers` | HTTP translation only. Calls services/selectors. | services, selectors |
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

## Auth and roles

* JWT via simplejwt: short-lived access token, rotating refresh token, old refresh tokens
  blacklisted (`token_blacklist`; expired rows are pruned daily by a beat task).
* Emails are lowercased in `accounts.services`, not constrained in the DB. Create users through
  `User.objects.create_user` or `register_user`, never `User(...)`.
* Email verification uses a signed, expiring token bound to the current address (no table). The
  `EmailVerificationRequested` event triggers a Celery task that sends the mail.
* Roles are implied by profiles. `talent` and `recruiters` register a predicate with
  `accounts.roles.register_role` in `ready()`, and `/api/me` evaluates them per request. Roles are
  not in the JWT. Permission classes live in the app that owns the rule (`IsTalent`,
  `IsRecruiter`, `IsVerifiedRecruiter`, `IsEmailVerified`). `common.permissions.IsStaff` guards
  staff-only endpoints; the services still re-check `is_staff` themselves.
* Services raise `Unauthenticated` for bad credentials; the API maps it to 401.

## Configuration

Environment via `django-environ`; settings split `base/local/test/production`.
Tunables (`AVAILABILITY_WINDOW_DAYS`, `OFFER_DEFAULT_TTL_DAYS`, cache TTLs,
`PLATFORM_COMMISSION_BPS`) live in `base.py`, not in code.

## Conscious limitations (to revisit)

* Event bus is in-process; move `_dispatch` onto Celery when volume or reliability needs it
  (add an outbox table if you need guaranteed delivery).
* `ATOMIC_REQUESTS` is on; domain errors are returned as responses (not raised through the
  request), so a failed request still commits earlier writes. Services must remain internally
  atomic (they are).
* Login has IP-scoped throttling only; there is no per-account lockout yet.
* Registering with a taken email returns 409, which reveals that the account exists.
* Engagement, ledger, payouts and disputes (M5/M6) are not part of this drop.
