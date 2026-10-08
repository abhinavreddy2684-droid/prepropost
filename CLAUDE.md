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
5. OpenAPI updated for any changed endpoint (`@extend_schema`; check `/api/docs/`).
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
`recruiters` <- `hiring`. `notifications` and `analytics` only subscribe to `hiring.events`.

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
- Only `READY` gallery media is public. The profile photo is a `Media` row with `purpose=avatar`.
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
make lint && python manage.py makemigrations --check --dry-run && pytest
```

`CREATEDB` is required because pytest-django creates `test_prepropost`.

## Roadmap (context for planning)

- Done (`v0.1.0`): foundation, data model, reference data and bootstrap endpoint, profiles,
  search selector, offer lifecycle, notifications, analytics events.
- Done (M1.1): JWT auth (simplejwt, rotating blacklisted refresh), email verification, talent
  onboarding + profile + experience + availability endpoints, OpenAPI. Phone OTP deferred.
- Done (M1.4): recruiter profile endpoints (`/api/recruiters/me`, submit for review), staff review
  queue with approve/reject endpoints and Django admin actions. Email must be verified to submit;
  approval is manual. No recruiter events yet (approve/reject notify nobody), and approval has no
  revoke or re-verification-on-edit.
- M2: media upload via pre-signed URLs and background processing. M3: search endpoints and
  ranking. M4: offer and project endpoints plus an offers inbox. M5: engagement, escrow and ledger
  (double-entry, idempotent, webhook-driven). M6: payouts, KYC, disputes, admin tooling.
- Product decisions already made: recruiter approval only (no auto-approval, with a talent
  escalation path); admin-mediated disputes; no free-form chat in MVP-A; contact details revealed
  only after funding; tiered KYC with talent KYC required before funding.

## Frontend contract

The React frontend is at `abhinavreddy2684-droid/PR.Pre.Pro.Post` (folder `24-crafts`). It loads
reference data once from `GET /api/bootstrap` into Redux, refers to crafts by `slug`, and
revalidates with the ETag. Keep API responses stable and documented through OpenAPI
(`/api/docs/`) so a typed client can be generated.

## When unsure

Ask before: changing the data model, adding a dependency, altering a public API contract, or
touching anything that moves money. Prefer the smallest change that fits the existing layers.
