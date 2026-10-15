# CLAUDE.md

Guidance for working on **Pre Pro Post**, a two-sided marketplace where cinema talent builds a
profile and portfolio, and recruiters discover talent and send offers. An accepted offer is a
*successful discovery*. Funded and completed work (milestone M5+) is a *successful hire*, with a 5%
platform commission.

Stack: Python 3.12, Django 5.2, DRF, PostgreSQL, Redis, Celery. Read `ARCHITECTURE.md` before
making structural changes.

## Commands

```bash
make up                      # api + worker + beat + db + redis (migrates and seeds on start)
make test                    # pytest in Docker
make fmt                     # ruff format + autofix
make lint                    # ruff check + format check (what CI runs)
make schema                  # regenerate openapi.yaml; commit it with any endpoint change
make schema-check            # fail if the committed openapi.yaml is stale (what CI runs)

# without Docker (needs a local Postgres)
export DJANGO_SETTINGS_MODULE=config.settings.test DJANGO_SECRET_KEY=x
export DATABASE_URL=postgres://prepropost:prepropost@localhost:5432/prepropost
python manage.py migrate && python manage.py seed_reference
pytest                       # whole suite
pytest apps/hiring -k expire # a subset
python manage.py makemigrations --check --dry-run   # must report "No changes detected"
```

Tests require PostgreSQL. The schema uses array fields, partial unique indexes and GIN indexes,
so SQLite will not work.

## Definition of done (run before EVERY commit)

1. `ruff format . && ruff check .` is clean.
2. `pytest` passes.
3. `makemigrations --check --dry-run` reports no changes.
4. New behaviour has tests. Bug fixes start with a failing test.
5. Any added or changed endpoint has `@extend_schema` (check `/api/docs/`), and `make schema` has
   regenerated `openapi.yaml`, committed in the same commit as the endpoint change. CI runs
   `make schema-check` and fails if the committed file is stale, which blocks the PR. Never edit
   `openapi.yaml` by hand.
6. Docs are updated if a convention or public contract changed.

Never commit with failing tests. Never skip hooks or CI.

## Architecture rules

Every app under `apps/` is a bounded context with the same layers:

| File | Does | Must not |
|---|---|---|
| `models.py` | Schema, DB constraints, enums | contain business logic |
| `selectors.py` | Read queries | write, or hide side effects |
| `services.py` | Write use cases: atomic, validated, authoritative | know about HTTP |
| `events.py` / `subscribers.py` | Publish and react to domain events | import the publisher's internals |
| views / serializers | Translate HTTP to services and back | contain business rules |
| `admin.py` | Staff tooling | change workflow state except via services |

Dependency direction (no cycles): `common` <- `accounts`, `reference` <- `media_library`, `talent`,
`recruiters` <- `hiring`. `notifications` subscribes to `hiring.events`, `recruiters.events` and
`talent.events`; `analytics` only to `hiring.events`.

- **Cross-app access goes through the other app's `selectors`/`services`.** Never query another
  app's tables directly. Example: hiring calls `recruiters.selectors.is_verified_recruiter`.
- **Status fields change only through services**, using `apps.common.state_machine.StateMachine`.
  Add a transition by adding a row to the app's transition table, never by `obj.status = ...`.
- **Raise `DomainError` subclasses** (`ValidationFailed`, `PermissionDenied`, `NotFound`,
  `Conflict`, `InvalidTransition`) from `apps.common.exceptions`. The API layer maps them to HTTP in
  one place. Do not raise DRF exceptions from services.
- **Services must stay internally atomic** (`@transaction.atomic`). `ATOMIC_REQUESTS` is on and the
  exception handler returns domain errors as responses, so a service cannot rely on the request
  transaction rolling back for it.
- **Lock before you change state:** `select_for_update()` on the row, then check status, then apply.
- **Signed GET URLs** come from `media_library.selectors.signed_urls`, which only signs. Issue
  them only for public items, to the owner, or to staff; never return a signed URL from a
  serializer that can see non-public media.
- **Media has three layers.** *Core* (`media_library`) owns bytes and their lifecycle (upload,
  confirm, processing, purge); it never imports an owning app and has no moderation. *Policy*
  (`media_library.policies`) is per-purpose data only: types, size per type, in-flight cap,
  thumbnail sizes, metadata stripping, with numbers read from settings. *Owning apps* (today
  `talent`) own meaning: items, links, moderation, likes and their own quotas. Owners tell core
  when bytes are no longer needed (`services.discard`) and subscribe to `media_ready` /
  `media_failed` / `media_expired`, ignoring purposes that are not theirs. Only
  `media_library/storage.py` imports boto3.
- **Registering a new media purpose** (e.g. `recruiters.logo`): in the owning app, build a
  `MediaPolicy` from settings and `register_policy(...)` it in `AppConfig.ready()`; add a model
  that points at `Media` (OneToOne); call `media_library.services.request_upload` from your own
  service (lock your profile row first); subscribe to the media events for your purpose; and
  decide your own visibility and moderation rules. Core does not change.
- **Moderation** (talent): gallery items `PENDING_REVIEW -> APPROVED | REJECTED`,
  `APPROVED -> HIDDEN -> APPROVED`; avatars `PENDING_REVIEW -> APPROVED | REJECTED`. REJECTED is
  final. Only staff, only through `talent.services` (`approve_item`, `reject_item`, `hide_item`,
  `restore_item`, `approve_avatar`, `reject_avatar`); reject and hide need a reason. The admin
  review queues call these services; no admin code writes a status.
- **Cross-module reactions use events** (`apps.common.events.publish`/`subscribe`). Events carry
  plain ids, never model instances, and are dispatched after commit.
- **Reference data (crafts, states, cities) lives only in the database.** Do not hardcode lists.
  Add data through admin or `seed_reference`. Reference rows are identified by `slug`, never by
  display name. Apps contribute enums to `/api/bootstrap` with
  `apps.reference.registry.register_enum` in `AppConfig.ready()`.
- **Tunable values live in `config/settings/base.py`** (availability window, offer TTL, cache TTLs,
  commission). Do not scatter magic numbers.
- Put integrity in the database as well as in Python: `CheckConstraint(condition=...)` and
  `UniqueConstraint`, so rules hold under concurrency.

## Domain rules that must not be broken

- **One account, many roles.** `User` is a login only. `TalentProfile` and `RecruiterProfile` are
  optional one-to-one profiles. A user may hold both, but cannot send an offer to themselves.
- **Privacy:** these talent fields are searchable but must NEVER appear in a public response:
  `full_name`, `gender`, `date_of_birth`, email, phone. Use separate public and private serializers
  and add a test that the public one omits them.
- **Money is integer minor units** (paise) plus a currency code. Never floats. Commission is
  computed once, with integer maths, and snapshotted on the engagement at funding time.
- **Availability:** only `tentative` and `unavailable` days are stored; "available" is implicit. A
  talent card shows Unavailable only if all of the next 7 days are unavailable.
- **Exactly one primary craft per talent** (enforced by a partial unique index).
- **A published profile stays publishable.** Talent services refuse (400 `profile_incomplete`)
  any edit that newly removes an item in `TALENT_REQUIRED_FOR_PUBLISH`; unpublish first. Call
  `_blocking_before` / `_require_still_publishable` in any new service that can remove one.
- **Gallery visibility:** an item is public only if `deleted_at` is null, `moderation_status` is
  `APPROVED`, and it is a link or its media is `READY`. The rule lives in
  `talent.selectors._public_items_q`; every public read (gallery, likes, M3 talent detail) goes
  through it. The owner sees all their non-deleted items with a derived stage; staff see everything.
- The profile photo is `TalentProfile.avatar` -> an approved `AvatarSubmission`. It is set only by
  approval; the previous photo stays visible while a new one is in review.
- **Media lock order, always:** `TalentProfile` row first, then the `User` row (taken inside
  `media_library.services.request_upload`), then the `Media` row. Never lock a profile after a
  Media row.
- Offers are terms snapshots. Do not edit an offer's terms after it is sent.

## Testing conventions

- `pytest` + `pytest-django` + `factory_boy`. Factories live in each app's `tests/factories.py`.
- Test services and selectors directly. Add API tests for permissions and the response shape.
- To assert event side effects use the `django_capture_on_commit_callbacks(execute=True)` fixture.
- Time-dependent services accept `today`/`now` parameters. Prefer passing them over freezing time.
- Tests inside one app must not depend on enums or data registered by apps that come later in the
  dependency order. Use the shared `isolated_registry` fixture (root `conftest.py`).
- Do not edit existing migrations once pushed. Add a new one. Review generated migrations before
  committing.

## Git workflow

- **Never commit to `main` directly.** Work on short-lived branches: `feat/...`, `fix/...`,
  `chore/...`, `docs/...`. Open a pull request. CI must pass before merge.
- **Commits are small and each leaves the tree green** (lint + tests pass at every commit).
- Use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): imperative summary`
  under ~72 characters, then a body explaining **why**.
  - types: `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `chore`
  - scopes: `common`, `accounts`, `reference`, `media`, `talent`, `recruiters`, `hiring`,
    `notifications`, `analytics`, or a new app name
  - Good: `fix(hiring): record expiry when a lapsed offer is accepted`
  - Bad: `updates`, `wip`, `fix stuff`
- Keep a feature's tests in the same commit as the feature. Do not mix refactors with behaviour
  changes.
- Do not rewrite history on pushed shared branches. Do not force-push to `main`.
- Never commit secrets. `.env` is git-ignored. Update `.env.example` when adding a variable.

## Working in cloud sessions

- `main` is protected by a ruleset: PR required, status check `test` must pass, branch must be up
  to date with `main`. Never push to `main`. Open a PR and do not merge it yourself.
- Scope, pacing, locked decisions and open items live in `docs/DELIVERY_PLAN.md`. Do not build
  anything marked "ASK BEFORE STARTING" without approval.
- Stop and ask when a decision is not covered by this file or the plan: data model changes, new
  dependencies, API contract changes, anything touching money.
- Docker is not available. Postgres 16 is preinstalled; Redis is not needed (test settings blank
  `REDIS_URL` and run Celery eagerly). Get to a green `pytest` with:

```bash
service postgresql start
su postgres -c "psql -c \"CREATE ROLE prepropost LOGIN PASSWORD 'prepropost' CREATEDB;\" \
  -c \"CREATE DATABASE prepropost OWNER prepropost;\""   # first run only
python3.12 -m venv /tmp/venv && . /tmp/venv/bin/activate   # CI uses 3.12
pip install -r requirements/dev.txt
export DJANGO_SETTINGS_MODULE=config.settings.test
export DJANGO_SECRET_KEY=cloud-session-secret-key-at-least-32-bytes   # short keys spam JWT warnings
export DATABASE_URL=postgres://prepropost:prepropost@localhost:5432/prepropost
make lint && make schema-check && python manage.py makemigrations --check --dry-run && pytest
```

`CREATEDB` is required because pytest-django creates `test_prepropost`.

## Roadmap (context for planning)

Full plan, pacing and open items: `docs/DELIVERY_PLAN.md`. Current state:

- Done (`v0.1.0`): foundation, data model, reference data and bootstrap endpoint, profiles,
  search selector, offer lifecycle, notifications, analytics events.
- Done (M1.1-M1.3): JWT auth (simplejwt, rotating blacklisted refresh), email verification,
  crafts and locations, talent onboarding + profile + experience + availability endpoints,
  OpenAPI. Phone OTP deferred.
- Done (M1.4): recruiter profile endpoints (`/api/recruiters/me`, submit for review), staff review
  queue with approve/reject endpoints and Django admin actions. Email must be verified to submit;
  approval is manual. Follow-up done: the pending queue is ordered by `submitted_at`; an approved
  recruiter who edits an identity field (name, company, type, website; case-insensitive) goes back
  to pending; `RecruiterVerified`/`RecruiterRejected` events drive an in-app notification and an
  email. Revoke/suspend is not built (M6.4). Talent services got the same hardening (row lock on
  edit, text caps, lifecycle fields read-only in admin).
- Done (M1.5): profile completeness. Ten items (`talent.selectors.CompletenessItem`) weighted
  by `TALENT_COMPLETENESS_WEIGHTS`; `gallery` counts only once an item is public. The private
  profile returns `percent`, `missing`, `blocking`, `can_publish` and `next_steps` (label, hint,
  weight, blocking). Publishing still needs only `TALENT_REQUIRED_FOR_PUBLISH` (primary craft,
  city).
- M2 media, phase 1 done (`feat/m2-media-core`): byte-lifecycle core with policies, S3 adapter
  (MinIO locally, see `docs/MEDIA_STORAGE.md`), pre-signed upload + confirm/cancel/upload-url,
  gallery items, links, avatar submissions, both quotas, expiry and purge jobs. Phase 2 done
  (`feat/m2-media-processing`): `process_media` worker (type sniffing, metadata stripping,
  thumbnails), refused-upload notifications, signed GET URLs, public gallery and likes. Phase 3
  done (`feat/m2-media-moderation`): moderation services and events, admin review queues, email
  and in-app notices. M2 is complete.
- M3-M4 are partly built below the API: `search_talents`, and the hiring services, events and
  notifications exist, but have no endpoints yet (see `docs/CODE_WALKTHROUGH.md` section 7). M3:
  search endpoints (advanced ranking deferred). M4: project and offer endpoints plus an offers
  inbox.
- MVP-B (M5 escrow and ledger, M6 payouts, KYC, disputes, admin): **ask before starting.**
  M7: hardening and launch.
- Product decisions are locked in the plan; do not reopen them without asking.

## Frontend contract

The React frontend is at `abhinavreddy2684-droid/PR.Pre.Pro.Post` (folder `24-crafts`). It loads
reference data once from `GET /api/bootstrap` into Redux, refers to crafts by `slug`, and
revalidates with the ETag. Keep API responses stable and documented through OpenAPI
(`/api/docs/`) so a typed client can be generated. The committed `openapi.yaml` (OpenAPI 3.0.3,
from drf-spectacular) is that contract: the typed client and the Postman collection are built
from it, so it must always match the code on `main`.

## When unsure

Ask before: changing the data model, adding a dependency, altering a public API contract, or
touching anything that moves money. Prefer the smallest change that fits the existing layers.
