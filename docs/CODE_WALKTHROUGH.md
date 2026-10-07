# Code walkthrough (backend as of M1.4)

Progress (resume point):
- [x] 1. Architecture, 2. event bus (core)
- [x] identity/auth (`apps/accounts`)
- [x] profiles (`apps/talent`, `apps/recruiters`, `apps/reference`)
- [x] media (`apps/media_library`)
- [x] discovery (search selectors in `apps/talent`)
- [x] projects/offers (`apps/hiring`)
- [x] notifications + analytics
- [x] 7. Built but not exposed, 8. Gaps and risks, final event table

Layout note: the doc is grouped per module. Each module section holds its endpoints, services, selectors and models (your sections 3 to 6), so you read one module top to bottom.

## 1. Architecture

- Request path: `config/urls.py` -> app `urls.py` -> APIView (`views.py`) -> serializer -> service (writes) or selector (reads) -> model. Only `accounts`, `reference`, `talent`, `recruiters` are mounted. `hiring`, `media_library`, `notifications`, `analytics` have no URLs.
- Serializers do shape and type checks only. Business validation is in services (e.g. `RegisterSerializer` is bare `CharField`s; `accounts.services.register_user` validates).
- Auth: default `JWTAuthentication` + `IsAuthenticated` (base.py REST_FRAMEWORK). Public views override with `authentication_classes=[]` and `AllowAny` (`accounts.views._PublicView`). Extra gates are permission classes (`IsEmailVerified`, `IsStaff`, plus talent/recruiter ones) or checks inside services.
- Transactions: `ATOMIC_REQUESTS=True` wraps every view in a transaction, and services add their own `@transaction.atomic`. Because the exception handler turns `DomainError` into a normal response, the request transaction does NOT roll back on a domain error. Services must raise before writing, or roll back themselves.
- Errors: services raise `DomainError` subclasses (`common/exceptions.py`). `common.api.exception_handler` maps ValidationFailed 400, Unauthenticated 401, PermissionDenied 403, NotFound 404, Conflict/InvalidTransition 409. All errors leave as `{"error": {"code","message","details"}}`. DRF-native errors (serializer validation, throttle, 401 from JWT) are re-wrapped into the same envelope with `details` = DRF's payload.
- State changes: `common.state_machine.StateMachine.next_state(current, event)` looks up `(state, event)` and raises `InvalidTransition` otherwise. Used by hiring offers and recruiter verification.
- Base models (`common/models.py`): `BaseModel` = UUID pk + created_at/updated_at; `SoftDeleteModel` (default manager hides `deleted_at`; `all_objects` shows all); `ReferenceModel` (slug, sort_order, is_active).
- Pagination: `DefaultCursorPagination` (page 24, max 100, `?limit=`, ordering `-created_at`) is the DRF default. Throttles: anon 120/min, user 600/min, plus scoped `auth_login` 10/min, `auth_register` 5/min, `auth_email_resend` 3/min.
- Health: `GET /healthz` (liveness), `GET /readyz` (DB `SELECT 1` and cache round trip, 503 on failure) in `common/views.py`.

## 2. Event bus (`apps/common/events.py`)

- Declaring: frozen dataclass subclasses of `DomainEvent` with a class-level `name` string. Fields are plain ids. Defined in `accounts/events.py` and `hiring/events.py`.
- Registering: `@subscribe(EventA, EventB)` on a plain function. It appends to a module-level `_subscribers` dict (deduped). Handlers register at import time, and each subscriber module is imported from its `AppConfig.ready()` (accounts, notifications, analytics). Event classes are matched by exact `type(event)`, so there is no inheritance dispatch.
- Publishing: `publish(event)` does `transaction.on_commit(lambda: _dispatch(event))`. It runs after commit, never inside the transaction. Outside a transaction `on_commit` runs immediately.
- Dispatch is synchronous and in-process, in the web/worker process that committed. Handlers run in order of registration.
- Failure handling: each handler is wrapped in try/except, logged with `logger.exception`, and swallowed. There is no retry, no outbox, and no dead-letter. A crash between commit and dispatch (process kill) loses the event silently. The docstring says `_dispatch` could later enqueue to Celery.
- Handlers that need slow work enqueue Celery themselves (accounts: `send_verification_email.delay`).
- Event list is at the end of the doc (section "Event table").

---

## Module: identity/auth (`apps/accounts`)

### Models
- `User(BaseModel, AbstractBaseUser, PermissionsMixin)`: `email` unique (lowercased and stripped by `UserManager.normalize_email`), `phone` unique/null (unused), `email_verified_at`, `phone_verified_at` (unused), `status` active/suspended, `is_staff`. `is_active` is a property (status == active), so suspended users cannot authenticate. `USERNAME_FIELD=email`. Index on `status`. Roles are not stored (see `roles.py`).

### Roles (`roles.py`)
- `register_role(name, predicate)` is called in `talent.apps` ("talent") and `recruiters.apps` ("recruiter"). `roles_for(user)` evaluates the predicates at request time. No JWT claims.

### Endpoints
All under `/api/`. Credential views extend `_PublicView` (no auth, `AllowAny`, `ScopedRateThrottle`).

**POST /api/auth/register** (public, throttle `auth_register` 5/min)
- Request: `email` (CharField max 254), `password` (max 128, whitespace kept, write-only). Serializer only checks presence/length.
- Flow:
  1. `RegisterView.post` -> `RegisterSerializer.is_valid(raise_exception=True)` (DRF 400 wrapped into the envelope).
  2. `services.register_user(email, password)`, `@transaction.atomic`:
     - lowercases/strips email; `django.core.validators.validate_email` -> else `ValidationFailed` ("Enter a valid email address", details.email)
     - `validate_password(password, user=User(email=email))` runs `AUTH_PASSWORD_VALIDATORS` -> else `ValidationFailed("Password is too weak.", details.password)`
     - `selectors.email_in_use` (`email__iexact` exists) -> `Conflict(code="email_taken")`
     - inner `transaction.atomic()` savepoint: `User.objects.create_user(email, password)` which runs `set_password` (Django's default hasher, PBKDF2 unless changed), `save`, defaults is_staff/is_superuser False. `IntegrityError` (race on the unique index) -> same `Conflict email_taken`.
     - publishes `UserRegistered(user_id)` and `EmailVerificationRequested(user_id)`, both deferred to commit.
  3. After commit: `accounts.subscribers._send_verification_email` (only subscriber of `EmailVerificationRequested`) calls `tasks.send_verification_email.delay(user_id)`. The Celery task loads the user via `selectors.get_user`, skips if missing or verified, renders `accounts/email/verify_email.txt` with `build_verification_url`, and `send_mail(...)` (console backend by default).
  4. Verification token (`build_verification_token`): `django.core.signing.dumps({"uid","email"}, salt="accounts.email-verification")`. Signed, timestamped, not stored in the DB. URL is `{FRONTEND_URL}/verify-email?token=...`.
  5. `services.issue_tokens(user)` -> `RefreshToken.for_user` (simplejwt; writes an OutstandingToken row via the blacklist app) -> `{access, refresh}`.
  6. Response 201: `{access, refresh, user:{id,email,email_verified,roles}}`; `roles` is `[]` at this point. No subscriber currently acts on `UserRegistered`.
- Errors: 400 (serializer, invalid email, weak password), 409 `email_taken`, 429 throttle.
- Note: the user is logged in immediately and the email is unverified. Gated actions use `IsEmailVerified`.

**POST /api/auth/login** (public, `auth_login` 10/min)
- Request: `email`, `password`. `services.login_user` (atomic): `django.contrib.auth.authenticate(email=normalized, password)`; `None` -> `Unauthenticated("Invalid email or password.")` (same message for unknown email, wrong password and suspended, since `is_active` is False). Then `update_last_login`. View returns `issue_tokens` -> `{access, refresh}`. No events.
- Errors: 401 with `WWW-Authenticate: Bearer` header (added in the exception handler), 400, 429.

**POST /api/auth/refresh** (`RefreshView(TokenRefreshView)`, scope `auth_login`)
- simplejwt's view. With `ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION` it blacklists the old refresh and returns a new pair. Access lifetime 15 min and refresh 14 days (env-tunable). It does not extend `_PublicView`; simplejwt's base view already disables authentication and permissions. No service in our code. Errors are simplejwt's 401 (wrapped by the handler).
- Gap: `RefreshView` has no `@extend_schema`.

**POST /api/auth/logout** (public, `auth_login`)
- Request: `refresh`. `services.logout` (atomic): `RefreshToken(refresh).blacklist()`, with `TokenError` suppressed, so it is idempotent and an invalid token is a silent 204. Access tokens stay valid until expiry (15 min).

**POST /api/auth/verify-email** (public, `auth_login`)
- Request: `token`. `services.verify_email` (atomic): `signing.loads(token, salt, max_age=EMAIL_VERIFICATION_TTL_HOURS*3600)`. `SignatureExpired` -> `ValidationFailed(code=token_expired)`, `BadSignature` -> `invalid_token`. Then `User.objects.select_for_update().filter(pk=uid)`; user missing or `user.email != payload.email` -> `invalid_token`. Already verified -> returns the user (idempotent, no event). Otherwise sets `email_verified_at`, saves `update_fields=[email_verified_at, updated_at]`, publishes `EmailVerified(user_id)` (no subscriber yet). Response: `_me(user)`.

**POST /api/auth/resend-verification** (authenticated, `auth_email_resend` 3/min)
- No body. `services.request_email_verification(user=request.user)` (atomic): verified -> `Conflict(code=already_verified)`, else publishes `EmailVerificationRequested` (-> the same Celery email path). 204.

**GET /api/me** (authenticated)
- `MeView.get` -> `_me(request.user)`: `{id, email, email_verified, roles}` with `roles_for(user)` evaluating each registered predicate (one query per role, no cache).

### Services (`accounts/services.py`)
- `register_user(email, password) -> User`: validates, inserts User; emits `UserRegistered`, `EmailVerificationRequested`.
- `login_user(email, password) -> User`: authenticates; updates `last_login`.
- `issue_tokens(user) -> dict`: mints a refresh and access pair; writes an OutstandingToken row.
- `logout(refresh_token)`: blacklists the token (DB write to BlacklistedToken); no-op on invalid.
- `build_verification_token(user)` / `build_verification_url(user)`: pure; signed token and the frontend link.
- `request_email_verification(user)`: emits `EmailVerificationRequested`; 409 if verified.
- `verify_email(token, now=None) -> User`: locks the user row, sets `email_verified_at`, emits `EmailVerified`.

### Selectors (`accounts/selectors.py`)
- `email_in_use(email)`: `User.objects.filter(email__iexact=email.strip()).exists()`.
- `get_user(user_id)`: `User.objects.filter(pk=...).first()`; no select_related; None if missing.

### Other
- Permissions: `accounts.permissions.IsEmailVerified` (code `email_not_verified`). Celery beat: `flush_expired_tokens` runs `flushexpiredtokens` (in `tasks.py`).

---

## Module: profiles

### Talent (`apps/talent`)

Permissions: `IsTalent` (`has_talent_profile(request.user)`: one `TalentProfile` exists query per request). `_OWN = [IsAuthenticated, IsTalent]`. `_own(request)` re-queries the profile in each view (a second query). Every write endpoint returns `_private(talent.pk)`, which re-fetches via `selectors.load_profile` (`select_related city__state`, `prefetch talent_crafts__craft, experiences__craft`) and serializes with `PrivateTalentSerializer`. `completeness` costs 2 extra queries (primary-craft exists, experience exists). None of the talent writes require a verified email, and none emit events.

Response shape of private profile: public fields (`id, professional_name, bio, city{id,slug,name,state_slug,state_name}, years_experience, genres, avatar_id, crafts[{slug,title,is_primary}], experiences[{id,title,company,start_year,end_year,description,craft}]`) plus `full_name, gender, date_of_birth, is_published, kyc_status, onboarding_completed, completeness{percent,missing,blocking,can_publish}`. The public serializer is a field whitelist, so private fields cannot leak by accident.

**POST /api/talent/me** (any authenticated user; no `IsTalent`, no verified email)
- Request: `full_name`, `professional_name` required. Optional `bio` (max 2000), `city_id`, `years_experience`, `genres` (max 20 items of 40 chars), `gender`, `date_of_birth`. Serializer is `TalentCreateSerializer` (required flags flipped in `__init__`).
- Flow: `_profile_fields` turns `city_id` into a City via `reference.selectors.get_active_city` (unknown or inactive -> `ValidationFailed "Unknown city."`). `services.create_talent_profile` (atomic): exists-check -> `Conflict`; builds a `TalentProfile`, `_apply_fields` (rejects unknown keys, `normalize_tags(genres)`, DOB must be before today), `_validate_profile` (names non-empty after strip and <=150, gender in enum, years 0..80), then `save()`. Profile starts `is_published=False`, `kyc_status=not_started`. Returns 201 with the private profile.
- Errors: 400 (serializer; `ValidationFailed` from city, service), 409 (already has a profile; the OneToOne unique would otherwise 500 on a race).

**GET /api/talent/me** (`IsTalent`): `_private(_own(request).pk)`. 403 `talent_role_required` if no profile.

**PATCH /api/talent/me** (`IsTalent`): partial `TalentUpdateSerializer`, `_profile_fields`, `services.update_talent_profile(talent, data)` (atomic; same apply and validate; full `save()`, no row lock, so concurrent edits are last-write-wins). Allowed fields `_EDITABLE_FIELDS`. Cannot touch `is_published` or `kyc_status`.

**PUT /api/talent/me/crafts** (`IsTalent`)
- Request: `primary` (slug), `supporting` (list of slugs, default []). `_crafts` -> `reference.selectors.crafts_by_slug` (unknown slugs -> 400 with `details.unknown`).
- `services.set_talent_crafts` (atomic): primary in supporting -> 400; any inactive craft -> 400; `select_for_update` on the profile row; deletes TalentCraft rows not wanted; clears `is_primary` on the rest; `bulk_create` the new ones; sets `is_primary=True` on the primary. This ordering avoids tripping the `talentcraft_one_primary` partial unique index. `talent_type` and `attributes` are never written, so they stay empty.
- Side effect: removing a craft deletes the TalentCraft row, but `Experience.craft` still points at the Craft (not at TalentCraft), so an experience can reference a craft no longer on the profile.

**PUT /api/talent/me/avatar** (`IsTalent`): `media_id` (uuid or null). `services.set_avatar` (atomic, row lock): null clears; otherwise `media_library.selectors.get_ready_avatar(owner_id=talent.user_id, media_id)`, else `ValidationFailed code=invalid_avatar`. Saves `avatar`.

**POST /api/talent/me/onboarding/complete** (`IsTalent`): `services.complete_onboarding` (atomic, lock). If `onboarding_completed_at` is None: `_require_publishable` (`selectors.profile_completeness`, blocking items from `settings.TALENT_REQUIRED_FOR_PUBLISH` = primary_craft, city) -> `ValidationFailed code=profile_incomplete details.missing`; then sets `onboarding_completed_at=now`, `is_published=True`. Idempotent on repeat (no change).

**PUT /api/talent/me/publish** (`IsTalent`): `published` bool. `services.set_published` (atomic, lock): turning on requires onboarding complete (`code=onboarding_incomplete`) and publishable (`profile_incomplete`). Turning off is always allowed. Writes `is_published`.

**GET /api/talent/me/experiences** (`IsTalent`): `selectors.list_experiences` (`select_related craft`, order `-start_year,-created_at`); not paginated; returns a bare list.
**POST /api/talent/me/experiences**: required `title`, `start_year`; optional `company, end_year, description (max 2000), craft` (slug). `_experience_fields` resolves the craft slug via `crafts_by_slug` (unknown -> 400). `services.add_experience` (atomic): `_apply_experience` rejects unknown keys, empty title, start outside 1900..2100, end outside start..2100, and `craft` not on the talent's profile (`selectors.has_craft`, `code=craft_not_on_profile`). 201 with the experience.
**PATCH /api/talent/me/experiences/<uuid>**: `services.update_experience` (atomic; `select_for_update` filtered by talent -> `NotFound` for someone else's entry, so no cross-user leak). Same validation. 200.
**DELETE /api/talent/me/experiences/<uuid>**: `services.delete_experience`; zero rows deleted -> `NotFound`. 204.

**GET /api/talent/me/availability** (`IsTalent`): query `start`, `end` (default today to today+90). Range must be start<=end and <=366 days (`ValidationFailed` raised in the view, not a service). `selectors.availability_overrides_between(...).order_by("date")`. Returns `[{date,status}]` of stored (tentative/unavailable) days only.
**PUT /api/talent/me/availability**: `entries` (1..366 of `{date,status}`; status is ChoiceField over available/tentative/unavailable). The dict comprehension `{e["date"]: e["status"]}` silently keeps the last duplicate. `services.set_availability(talent, entries, today=None)` (atomic): every day must lie in `[today, today+89]` (`AVAILABILITY_WINDOW_DAYS`), else 400. `available` entries delete overrides in one query; others `update_or_create` one by one (N queries). 204.

**GET /api/talent/<uuid>** (public: no auth, `AllowAny`, default anon throttle): `selectors.get_public_talent` (published only; same select_related/prefetch) -> `NotFound` otherwise (unpublished and nonexistent look identical). `PublicTalentSerializer` (no private fields; there is a test requirement for this in CLAUDE.md). No availability in the public response.

### Recruiters (`apps/recruiters`)

Permissions: `IsRecruiter` (`recruiters.permissions`), `IsVerifiedRecruiter` (defined, used by hiring later), `IsStaff` (common) for the review endpoints. Output serializer `RecruiterProfileSerializer`: `id, display_name, company_name, recruiter_type, website, city{...}, verification_status, rejection_reason, verified_at, created_at, updated_at` (no `verified_by`). `_out(profile)` re-fetches via `get_recruiter_profile` (select_related city) for every response.

**POST /api/recruiters/me** (any authenticated; no email verification needed). Request: `display_name` required; optional `company_name, recruiter_type (CharField, validated in service), website (URLField), city_id`. `services.create_recruiter_profile` (atomic): unknown extras -> 400; exists -> `Conflict`; `_validate_profile` (strip, non-empty name, lengths, type in enum); `save()` with `verification_status=unverified`. 201.
**GET /api/recruiters/me** (`IsRecruiter`): `_out(...)`.
**PATCH /api/recruiters/me** (`IsRecruiter`): `update_recruiter_profile` (atomic, `select_for_update`, setattr, validate, `save(update_fields=[...data, updated_at])`). Does not touch verification (an approved recruiter can change company name and stay approved).
**POST /api/recruiters/me/submit** (`IsAuthenticated, IsRecruiter, IsEmailVerified`): `services.submit_for_verification` -> `_transition` (row lock, `VERIFICATION.next_state(status,"submit")`; allowed from unverified and rejected) -> clears `rejection_reason`. 409 `invalid_transition` from pending/approved. 403 `email_not_verified` / `recruiter_role_required` from permissions. No event.
**GET /api/admin/recruiters?status=pending** (`IsStaff`): `ListAPIView` with `_ReviewPagination` (cursor, ordering `(updated_at, id)`, page 24). `selectors.list_for_review(status)` (`select_related city__state, user`; oldest first). Unknown status -> 400. Items: profile fields plus `email`, `email_verified` of the user.
**POST /api/admin/recruiters/<uuid>/approve** (`IsStaff`): `get_recruiter_profile_by_id` (`NotFound`) -> `services.approve_recruiter(profile, reviewer)` (atomic; `_require_staff` again -> `PermissionDenied`; transition "approve" only from pending -> `InvalidTransition` 409; sets `verified_by`, `verified_at`). Re-fetches and returns `RecruiterReviewSerializer`. No event or notification.
**POST /api/admin/recruiters/<uuid>/reject** (`IsStaff`): body `reason` (max 255). `services.reject_recruiter`: staff check, reason strip/non-empty/<=255, transition "reject" from pending, sets `rejection_reason`, clears `verified_by`/`verified_at`. 200.

State machine (`recruiters/state_machine.py`): unverified/rejected -submit-> pending; pending -approve-> approved; pending -reject-> rejected. Approved is terminal (no revoke).

Admin (`recruiters/admin.py`, `hiring/admin.py`, etc.): staff tooling; the recruiter admin actions call services (approve, reject), per CLAUDE.md.

### Reference (`apps/reference`)

**GET /api/bootstrap** (public, cacheable): `BootstrapView`. `condition(etag_func=_etag)` calls `get_bootstrap().version` (so ETag/If-None-Match handled by Django `condition`; note it sends the raw version, and `cache_control(public, max_age=BOOTSTRAP_CLIENT_MAX_AGE_SECONDS)`). `get_bootstrap`: cache key `reference:bootstrap:<8-char hash of registered enums>`; on miss `_build_snapshot` = `serialize_crafts` + `serialize_locations` + `serialized_enums()`, version = sha256(canonical JSON)[:12]; cached for `REFERENCE_CACHE_TTL_SECONDS` (1h). Cache is invalidated by `signals._reference_changed` (post_save/post_delete on Craft, TalentType, State, City) via `invalidate_bootstrap_cache`, deferred to commit. Response: `{version, crafts[{id,slug,title,description,talent_types[]}], locations[{id,slug,name,cities[]}], enums{name:[{value,label}]}}`. Registered enums: `genders`, `availability_statuses` (talent), `recruiter_types` (recruiters), `project_types`, `offer_statuses` (hiring); media registers its own in its `apps.py`.

Services: `get_bootstrap()`, `invalidate_bootstrap_cache()`, `sync_crafts(items)` and `sync_locations(items)` (atomic idempotent upserts via `update_or_create` on slug, forcing `is_active=True`), used by `manage.py seed_reference` from `reference/seed_data/*.json`.
Selectors: `active_crafts` (`filter is_active`, `prefetch talent_types`), `active_states` (`prefetch cities`), `serialize_crafts`, `serialize_locations` (the inactive child filter is in Python), `get_active_city(city_id)` (city and its state both active), `crafts_by_slug(slugs)` (includes inactive crafts).

### Talent services (one line each)
- `create_talent_profile(user, full_name, professional_name, **extra)`: validated insert; no events.
- `update_talent_profile(talent, data)`: validated full save of editable fields.
- `set_talent_crafts(talent, primary, supporting)`: row lock, replace TalentCraft set (deletes, bulk_create, updates).
- `set_availability(talent, entries, today=None)`: validates window and status; deletes/`update_or_create` AvailabilityOverride.
- `set_avatar(talent, media_id=None)`: lock; checks media owner and READY via media selector; writes `avatar`.
- `complete_onboarding(talent, now=None)`: lock; checks completeness; sets onboarding timestamp and `is_published`.
- `set_published(talent, published)`: lock; guards on onboarding and completeness; writes `is_published`.
- `add_experience / update_experience / delete_experience`: validated writes (`update`/`delete` scoped to the owner, `NotFound` otherwise).

### Talent selectors (one line each)
- `search_talents(filters, today)`: see Discovery.
- `annotate_availability(qs, today)`: see Discovery.
- `availability_overrides_between(talent_id, start, end)`: overrides with `start<=date<=end` (unordered; the view orders).
- `has_craft(talent_id, craft_id)`: exists on TalentCraft.
- `get_talent_profile(user)` / `has_talent_profile(user)`: `TalentProfile.objects.filter(user=user).first()`; no select_related.
- `profile_completeness(talent)`: 2 queries (primary craft, any experience) plus field checks; returns `Completeness(percent, missing, blocking)`.
- `list_experiences(talent)`: described above; `get_experience(talent, id)`: **unused** (services query directly).
- `load_profile(talent_id)`: profile with display relations; `get_public_talent(talent_id)`: same, published only.

### Models (talent and recruiters)
- `TalentProfile` (BaseModel): `user` 1:1 (CASCADE), public `professional_name, avatar -> media_library.Media (SET_NULL), bio, city -> reference.City (PROTECT), years_experience, genres ArrayField`; private `full_name, gender, date_of_birth`; lifecycle `onboarding_completed_at, is_published, kyc_status`. Constraints: `talent_years_experience_sane` (null or <=80). Indexes: GIN on `genres`, `(is_published, city)`, `date_of_birth`.
- `TalentCraft`: `talent` FK, `craft` FK (PROTECT), optional `talent_type`, `is_primary`, JSON `attributes`. Constraints: unique `(talent, craft)`; unique `(talent)` where `is_primary` (exactly-one is not enforced at DB level, only at-most-one; "at least one" is a service rule and the publish gate).
- `Experience`: `talent` FK, optional `craft`, `title, company, start_year, end_year (null = present), description`. Checks: end>=start, start in 1900..2100. Ordering `-start_year`.
- `AvailabilityOverride`: `talent`, `date`, `status` in (tentative, unavailable). Unique `(talent, date)`, check status in those two, index `(talent, date)`.
- `RecruiterProfile`: `user` 1:1, `display_name, company_name, recruiter_type, website, city`, `verification_status` (indexed), `verified_by -> User (SET_NULL), verified_at, rejection_reason`. No DB check tying status to `verified_*`.
- Reference: `Craft` (unique title and slug), `TalentType` (FK craft; unique `(craft, slug)`), `State` (unique name and slug), `City` (FK state; unique `(state, slug)`). All `ReferenceModel` (slug, sort_order, is_active).

---

## Module: media (`apps/media_library`)

No URLs, views or serializers exist. The only live link to the rest of the system is `PUT /api/talent/me/avatar`, which calls `selectors.get_ready_avatar`. Everything else is unexposed (M2).

### Models
- `Media(BaseModel, SoftDeleteModel)`: `owner -> User (CASCADE)`, `purpose` gallery/avatar, `kind` video/audio/image/pdf/link, `status` pending_upload/processing/ready/rejected, `title, description, year, craft`, storage (`storage_key, thumbnail_key, external_url, mime_type, size_bytes, duration_seconds, metadata`), `rejection_reason`, `likes_count` (denormalized). The default manager hides soft-deleted rows.
- Constraints: link kind needs `external_url`; avatar purpose must be an image; `ready` needs `storage_key` unless a link; `size_bytes >= 0`. Indexes `(owner, status)` and `(craft, status)`.
- `MediaLike(user, media)`: unique `(user, media)`.
- Enum `media_kinds` is registered for the bootstrap payload (`apps.py`). `Purpose` and `Status` are not registered.

### Services (`services.py`)
- `like_media(user, media_id) -> Media`: atomic; `_get_public_media` (READY only, else `NotFound`); `get_or_create` MediaLike; if created, `F("likes_count") + 1` then refresh. Idempotent. No events.
- `unlike_media(user, media_id) -> Media`: same lookup; delete the like; if one was deleted, `F - 1`. Idempotent.
- No upload, processing, status-transition or delete services exist, and there is no media state machine.

### Selectors (`selectors.py`)
- `public_gallery(owner_id)`: `Media` where `owner=owner_id, purpose=gallery, status=ready`, ordered `-year, -created_at`; no pagination, no select_related. **Unused** (not in the public talent response).
- `get_ready_avatar(owner_id, media_id)`: avatar-purpose READY row owned by that user, `.first()`. Used by `talent.services.set_avatar`.

### Admin
- `MediaAdmin` (`all_objects`, so soft-deleted rows show) has actions `mark_ready` and `mark_rejected` that use `queryset.update(status=...)`.

---

## Module: discovery (`apps/talent/selectors.py`)

There is no search endpoint yet (M3). The only discovery read exposed is `GET /api/talent/<uuid>` (documented under Talent). What exists is the selector:

**`search_talents(filters: TalentFilters, *, today) -> QuerySet[TalentProfile]`**
- Base: `TalentProfile.objects.filter(is_published=True).select_related("city__state")`. No prefetch of crafts or experiences, so a card serializer that touches `talent_crafts` or the avatar will do N+1.
- `q`: `icontains` on `professional_name` OR `bio` OR `city__name`. `full_name` is deliberately not searched by `q`.
- `craft_slug`: `Exists(TalentCraft where talent=OuterRef(pk), craft__slug=...)`, with `is_primary=True` when `primary_craft_only`.
- `city_slug`, `state_slug` (`city__state__slug`), `gender` (private field, filter only), `genres` (`genres__overlap`, uses the GIN index).
- Age: `min_age` -> `date_of_birth <= years_before(today, min_age)`; `max_age` -> `date_of_birth > years_before(today, max_age+1)`. Null DOB rows drop out whenever either bound is set.
- `available`: `annotate_availability` counts UNAVAILABLE override rows with `today <= date < today+7` (`AVAILABILITY_CARD_LOOKAHEAD_DAYS`), and `is_available = blocked_days < 7`. This matches the "Unavailable only if all 7 days are blocked" rule. Tentative days do not count. It is a JOIN with `Count`, so combined with other joins it relies on Exists subqueries to avoid row multiplication (it does).
- Ordering: `-updated_at, id`. No relevance ranking, and `updated_at` changes on any edit. Not paginated here; the cursor pagination default orders by `-created_at`, which conflicts with this ordering unless the future view overrides it (the recruiter review view shows the override pattern).
- `TalentFilters` is a frozen dataclass; each filter = one field + one clause.

Related: `annotate_availability(qs, today)` (above); `common/dates.years_before(date, years)`; `common/text.normalize_tags` (used for genres on write).

---

## Module: projects and offers (`apps/hiring`)

**No endpoints.** `hiring` has no `urls.py`, `views.py`, `serializers.py`, `selectors.py` or permission classes. Only services, a Celery task and two registered enums (`project_types`, `offer_statuses`) exist. M4 must add all the HTTP and read side. The services take model instances (`project`, `talent`, `craft`), so views must translate ids to instances first (the talent views show the pattern).

### State machine (`hiring/state_machine.py`, `OFFER_MACHINE`)
draft -send-> sent; draft -withdraw-> withdrawn; sent -accept-> accepted; sent -decline-> declined; sent -expire-> expired; sent -withdraw-> withdrawn. Accepted, declined, expired, withdrawn are terminal. There is no project state machine (`Project.status` has no transitions in code).

### Services (`hiring/services.py`)
- `create_project(recruiter, **fields) -> Project`: atomic; requires a recruiter profile (`recruiters.selectors.has_recruiter_profile`, else `PermissionDenied`), not a verified one. No field validation (the DB check constraints on dates/budget would surface as a 500). Writes `Project` (status defaults to OPEN). No event.
- `create_offer(actor, project, talent, craft, amount_minor, deliverables, **terms) -> Offer`: atomic. Checks in order: project belongs to the actor (`PermissionDenied`); project not closed (`Conflict`); actor is a verified recruiter (`PermissionDenied`); talent is not the actor's own profile (`ValidationFailed`); talent `is_published`; `talent_selectors.has_craft`; amount > 0; non-blank deliverables. Inserts a DRAFT `Offer` with `currency` copied from the project inside a savepoint; `IntegrityError` from the `offer_one_live_per_project_talent_craft` partial unique index -> `Conflict`. No event. No `OfferEvent` row is written for the draft (history starts at send).
- `send_offer(actor, offer_id, ttl_days=None) -> Offer`: atomic; `_locked` (`select_for_update(of=self)` + `select_related talent`; `NotFound`), `_require_recruiter`, `_require_verified`, then `_apply("send")` with `sent_at=now`, `expires_at=now + (ttl_days or OFFER_DEFAULT_TTL_DAYS)`. Emits `OfferSent`.
- `accept_offer(actor, offer_id)` / `decline_offer(actor, offer_id, reason="")`: both go to `_respond`: lock, `_require_talent` (offer.talent.user_id == actor), and if the offer is SENT but past `expires_at`, it applies `expire` (writes + emits `OfferExpired`), commits, then raises `Conflict(code="offer_expired")`. Otherwise `_apply(accept|decline)` with `responded_at` (and `decline_reason` trimmed to 255). Emits `OfferAccepted` or `OfferDeclined`.
- `withdraw_offer(actor, offer_id)`: atomic; lock; `_require_recruiter` (no verified check); `_apply("withdraw")` with `responded_at`. Emits `OfferWithdrawn`. Allowed from draft and sent.
- `expire_due_offers(now=None) -> int`: reads ids of SENT offers with `expires_at <= now`, then per offer opens its own transaction, locks, re-checks status and expiry, `_apply("expire", actor=None)`. Emits `OfferExpired` (`actor_id=None`). Called by Celery task `hiring.tasks.expire_due_offers`, scheduled every 300 s in `CELERY_BEAT_SCHEDULE`.
- `_apply(offer, event, actor, **changes)` (internal, the only writer of `Offer.status`): `OFFER_MACHINE.next_state` (raises `InvalidTransition` 409), inserts an `OfferEvent` (from/to/actor) audit row, `setattr` changes, `save(update_fields=...)`, `publish(<event class for the event name>)` with ids only.

### Selectors
None in hiring. Cross-app reads go through `recruiters.selectors.has_recruiter_profile`, `recruiters.selectors.is_verified_recruiter` and `talent.selectors.has_craft`.

### Models
- `Project`: `recruiter -> User (PROTECT)` (a user, not a RecruiterProfile), `title, description, project_type, city, start_date, end_date, budget_minor (BigInt, null), currency (default INR), status` draft/open/closed. Checks: `end_date >= start_date`, budget >= 0. Index `(recruiter, status)`.
- `Offer`: `project` (PROTECT), `recruiter -> User`, `talent -> TalentProfile` (PROTECT), `craft` (PROTECT); snapshot terms `amount_minor, currency, deliverables, terms, start_date, deadline, revision_rounds`; `status`, `expires_at, sent_at, responded_at, decline_reason`. Checks: amount > 0, deadline >= start_date. Partial unique `(project, talent, craft)` where status in draft/sent/accepted (one live offer). Indexes `(talent, status)`, `(recruiter, status)`, `(status, expires_at)` (serves the expiry job). `LIVE_STATUSES` constant.
- `OfferEvent(UUIDModel)`: append-only history `offer, from_status, to_status, actor (null = system), created_at`; ordering `created_at`; index `(to_status, created_at)`. Note the name clash with the `hiring.events` domain-event classes (`OfferSent`, ...); these are different things.
- Admin: `OfferAdmin` is read-only with an event inline; `ProjectAdmin` is fully editable (including `status`).

---

## Module: notifications and analytics

Both are pure subscribers: `apps.py: ready()` imports `subscribers`, which registers handlers on `hiring.events` only. No URLs, no views.

- **notifications**: `Notification(user, kind, payload JSON, read_at)`, index `(user, read_at, -created_at)`.
  - `services.notify(user_id, kind, payload=None)`: one INSERT. `services.mark_read(user, notification_ids) -> int`: `UPDATE read_at=now` for the user's own unread rows. **Unused.**
  - Handlers (`notifications/subscribers.py`): `OfferSent` notifies the talent user; `OfferAccepted` and `OfferDeclined` notify the recruiter; `OfferWithdrawn` notifies the talent user; `OfferExpired` notifies both. `kind` = event `name`; payload `{offer_id, project_id}`. No email or push.
- **analytics**: `AnalyticsEvent(name, actor, subject_type, subject_id, props JSON, created_at)`, index `(name, created_at)`. `services.track(name, actor_id, subject_type, subject_id, **props)` inserts one row. One handler records all five offer events with `subject_type="offer"`, `props.project_id`. The docstring mentions `profile_viewed` and `search_run`, but nothing records them. Account events are not tracked.

---

## Event table

| Event (`name`) | Defined in | Published by | Subscribers |
|---|---|---|---|
| `UserRegistered` (`user_registered`) | `accounts/events.py` | `accounts.services.register_user` | none |
| `EmailVerificationRequested` | `accounts/events.py` | `register_user`, `request_email_verification` | `accounts.subscribers._send_verification_email` (queues Celery email) |
| `EmailVerified` | `accounts/events.py` | `verify_email` (not on the idempotent re-verify) | none |
| `OfferSent` (`offer_sent`) | `hiring/events.py` | `hiring.services.send_offer` via `_apply` | notifications (talent), analytics |
| `OfferAccepted` | `hiring/events.py` | `accept_offer` | notifications (recruiter), analytics |
| `OfferDeclined` | `hiring/events.py` | `decline_offer` | notifications (recruiter), analytics |
| `OfferWithdrawn` | `hiring/events.py` | `withdraw_offer` | notifications (talent), analytics |
| `OfferExpired` | `hiring/events.py` | `expire_due_offers`, and `_respond` on a lapsed offer | notifications (both parties), analytics |

Hiring events share `_OfferEvent` fields: `offer_id, project_id, recruiter_id, talent_user_id, actor_id`. Talent and recruiter services publish nothing (profile created, published, approved and rejected all notify nobody).

---

## 7. Built but not yet exposed

- **Hiring (M4)**: all of `create_project`, `create_offer`, `send_offer`, `accept_offer`, `decline_offer`, `withdraw_offer`, `expire_due_offers` (only the last runs, via beat). Missing: project CRUD and close, offer inbox selectors for both sides, offer detail with `OfferEvent` history, permissions (`IsVerifiedRecruiter` exists in `recruiters/permissions.py` but nothing imports it), serializers (money in minor units), urls.
- **Discovery (M3)**: `talent.selectors.search_talents`, `TalentFilters`, `annotate_availability` (nothing calls them). Missing: view, filter serializer, card serializer, pagination ordering, analytics `search_run`.
- **Media (M2)**: `like_media`, `unlike_media`, `public_gallery`, the whole `Media` lifecycle (upload, processing, status moves), `MediaLike`. Only `get_ready_avatar` is in use, and no way to create an avatar `Media` row exists except admin or shell.
- **Notifications**: all handlers run and rows are written, but there is no inbox endpoint, no `mark_read` caller, no unread count.
- **Events with no subscriber**: `UserRegistered`, `EmailVerified`.
- **Fields with no writer**: `User.phone`, `phone_verified_at`; `TalentProfile.kyc_status`; `TalentCraft.talent_type` and `attributes`; `Offer` terms beyond `amount`/`deliverables` (via `**terms`), `Project.status` transitions.
- **Selectors with no caller**: `talent.selectors.get_experience`, `media_library.selectors.public_gallery`.

## 8. Gaps and risks

- **Domain errors do not roll back the request transaction** (the custom handler never calls DRF's `set_rollback`). Any service that writes and then raises will commit the write. Current services raise before writing, except `_respond`, which relies on this to persist a lapse. Keep that in mind for M4 and M5.
- **Events are not durable.** `publish` uses `on_commit` plus in-process sync dispatch with swallowed exceptions. A crash after commit, or a handler error (e.g. Redis down on `send_verification_email.delay`), loses the notification, analytics row or email with only a log line. Registration returns 201 regardless; resend-verification is the recovery.
- **Media admin bypasses the rules.** `MediaAdmin.mark_ready` does `queryset.update(status=READY)`: no service, no state machine, and it ignores the `media_ready_has_storage_key` check constraint (an item without a key will raise `IntegrityError`, a 500 in admin). It contradicts the "admin changes state via services only" rule.
- **Unique-race 500s on profile creation.** `create_talent_profile` and `create_recruiter_profile` check existence then insert without catching `IntegrityError` (unlike `register_user` and `create_offer`). Concurrent POSTs would 500 on the 1:1 unique.
- **`create_offer(**terms)` and `create_project(**fields)` are unvalidated pass-throughs.** There is no allowlist (talent and recruiter services have one). A caller could pass `status`, `expires_at` or a duplicate `currency` kwarg. Dates and revision rounds hit DB check constraints as 500s rather than 400s. `create_project` needs only a recruiter profile, not an approved one.
- **Offer sending is lenient.** `send_offer` re-checks verification but not that the project is still open or the talent is still published, so a draft can go out after either changes. `withdraw_offer` has no verification check (probably fine). `ttl_days` has no upper bound or type check.
- **Search will be N+1 by default.** `search_talents` selects `city__state` only, so a card showing crafts, or an avatar, will query per row. Its ordering (`-updated_at, id`) conflicts with the default cursor paginator's `-created_at`, so the view must override ordering. `q` uses `icontains` over `bio` (no index).
- **Duplicate and redundant queries on the talent endpoints.** `IsTalent` queries the profile, `_own()` queries it again, and `_private()` queries it a third time with prefetches. `/api/me` runs one query per registered role. `set_availability` does one `update_or_create` per day (up to 366). Roles are not cached.
- **Shared throttle bucket.** `auth_login` (10/min) is the scope for login, logout, refresh and verify-email, so ScopedRateThrottle shares one counter per client across them. Normal refresh traffic can be throttled by login attempts.
- **Inconsistent patterns.** Some validation is in views instead of services (availability range in `AvailabilityView.get`, city/craft resolution), `update_talent_profile` does not lock the row while the recruiter equivalent does, profile edits by an approved recruiter keep approval, `RefreshView` has no OpenAPI schema, `get_experience` and `public_gallery` are dead code, and no talent or recruiter state change publishes events.
