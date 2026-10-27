"""Settings shared by every environment. Environment-specific files only override."""

from datetime import timedelta
from pathlib import Path

import environ
from corsheaders.defaults import default_headers as default_cors_headers

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]
THIRD_PARTY_APPS = [
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
]
LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.reference",
    "apps.media_library",
    "apps.talent",
    "apps.recruiters",
    "apps.hiring",
    "apps.notifications",
    "apps.analytics",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # before CommonMiddleware, which can answer early
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # store UTC; present in the client's zone
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Cache -----------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- DRF -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "apps.common.api.exception_handler",
    "DEFAULT_PAGINATION_CLASS": "apps.common.api.DefaultCursorPagination",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "600/min",
        # Scoped rates for credential endpoints (applied per view with ScopedRateThrottle).
        "auth_login": "10/min",
        "auth_register": "5/min",
        "auth_email_resend": "3/min",
    },
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=1)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}
# The refresh token travels only in this HttpOnly cookie, never in a response body. The path keeps
# it off every request except /api/auth/*. Production turns Secure on (production.py).
# SameSite=Lax needs the frontend and API on the same site; a cross-site deployment needs
# SameSite=None, which browsers only accept together with Secure.
AUTH_REFRESH_COOKIE_NAME = env("AUTH_REFRESH_COOKIE_NAME", default="refresh_token")
AUTH_REFRESH_COOKIE_PATH = env("AUTH_REFRESH_COOKIE_PATH", default="/api/auth/")
AUTH_REFRESH_COOKIE_SECURE = env.bool("AUTH_REFRESH_COOKIE_SECURE", default=False)
AUTH_REFRESH_COOKIE_SAMESITE = env("AUTH_REFRESH_COOKIE_SAMESITE", default="Lax")
AUTH_REFRESH_COOKIE_DOMAIN = env("AUTH_REFRESH_COOKIE_DOMAIN", default=None)
SPECTACULAR_SETTINGS = {
    "TITLE": "Pre Pro Post API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- Email -----------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Pre Pro Post <no-reply@prepropost.local>")
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173").rstrip("/")

# --- CORS ------------------------------------------------------------------
# Only the listed origins, never "allow all": the browser sends the refresh cookie on credentialed
# requests, so a wildcard origin would let any site drive a signed-in session.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[FRONTEND_URL])
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r"^/api/.*$"
CORS_EXPOSE_HEADERS = ["ETag"]
# /api/bootstrap is revalidated with If-None-Match, which is not CORS-safelisted, so the preflight
# must allow it on top of the defaults (authorization, content-type, ...).
CORS_ALLOW_HEADERS = (*default_cors_headers, "if-none-match")

# --- Celery ----------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_ACKS_LATE = True
CELERY_BEAT_SCHEDULE = {
    "expire-due-offers": {"task": "apps.hiring.tasks.expire_due_offers", "schedule": 300.0},
    "flush-expired-tokens": {
        "task": "apps.accounts.tasks.flush_expired_tokens",
        "schedule": 24 * 60 * 60.0,
    },
    "expire-pending-uploads": {
        "task": "apps.media_library.tasks.expire_pending_uploads",
        "schedule": 10 * 60.0,
    },
    "purge-discarded-media": {
        "task": "apps.media_library.tasks.purge_discarded_media",
        "schedule": 30 * 60.0,
    },
    "warn-stuck-media-processing": {
        "task": "apps.media_library.tasks.warn_stuck_processing",
        "schedule": 5 * 60.0,
    },
}

# --- Domain configuration (tunable without code changes) -------------------
REFERENCE_CACHE_TTL_SECONDS = 60 * 60
BOOTSTRAP_CLIENT_MAX_AGE_SECONDS = 300
AVAILABILITY_WINDOW_DAYS = 90
AVAILABILITY_CARD_LOOKAHEAD_DAYS = 7
EMAIL_VERIFICATION_TTL_HOURS = 48
# Completeness items a talent must have before the profile can be published (see
# apps.talent.selectors.CompletenessItem for the full list that feeds the percentage).
TALENT_BIO_MAX_CHARS = 1000  # API and service limit; the column stays an unbounded TextField
TALENT_REQUIRED_FOR_PUBLISH = ("primary_craft", "city")
# Share of the completeness percentage each item is worth. Every item must be listed.
TALENT_COMPLETENESS_WEIGHTS = {
    "primary_craft": 3,
    "city": 2,
    "avatar": 3,
    "gallery": 3,  # at least one approved, public gallery item
    "bio": 2,
    "years_experience": 1,
    "genres": 1,
    "experience": 2,
    "date_of_birth": 1,
    "gender": 1,
}
OFFER_DEFAULT_TTL_DAYS = 7
PLATFORM_COMMISSION_BPS = 500  # 5%; snapshotted per engagement at funding time (M5)

# --- Media -----------------------------------------------------------------
# One private S3-compatible bucket per environment: MinIO locally (ENDPOINT_URL set), AWS S3
# ap-south-1 in staging and production (ENDPOINT_URL empty). See docs/MEDIA_STORAGE.md.
MEDIA_STORAGE_BACKEND = env("MEDIA_STORAGE_BACKEND", default="apps.media_library.storage.S3Storage")
MEDIA_STORAGE = {
    "ENDPOINT_URL": env("MEDIA_STORAGE_ENDPOINT_URL", default=""),
    "PUBLIC_ENDPOINT_URL": env("MEDIA_STORAGE_PUBLIC_ENDPOINT_URL", default=""),
    "BUCKET": env("MEDIA_STORAGE_BUCKET", default="prepropost-media"),
    "REGION": env("MEDIA_STORAGE_REGION", default="ap-south-1"),
    "ACCESS_KEY_ID": env("MEDIA_STORAGE_ACCESS_KEY_ID", default=""),
    "SECRET_ACCESS_KEY": env("MEDIA_STORAGE_SECRET_ACCESS_KEY", default=""),
}
# Exact browser origins allowed to POST uploads to and GET media from the bucket. Never "*".
MEDIA_CORS_ALLOWED_ORIGINS = env.list("MEDIA_CORS_ALLOWED_ORIGINS", default=[FRONTEND_URL])
MEDIA_UPLOAD_URL_TTL = 600  # seconds a signed upload POST stays valid
MEDIA_PENDING_TTL = 3600  # seconds a PENDING_UPLOAD row may wait for its bytes
MEDIA_READ_URL_TTL = 900  # seconds a signed GET URL stays valid
MEDIA_MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
MEDIA_PROCESSING_STUCK_SECONDS = 600  # warn (for monitoring) after this long in PROCESSING
MEDIA_IMAGE_MAX_BYTES = 10 * 1024 * 1024
MEDIA_AUDIO_MAX_BYTES = 20 * 1024 * 1024
MEDIA_VIDEO_MAX_BYTES = 200 * 1024 * 1024
MEDIA_PDF_MAX_BYTES = 10 * 1024 * 1024
# Per-purpose policies (registered by the owning app) read these.
TALENT_AVATAR_MAX_IN_FLIGHT = 2
TALENT_AVATAR_THUMBNAIL_SIZES = (96, 400)
TALENT_GALLERY_MAX_IN_FLIGHT = 3
TALENT_GALLERY_THUMBNAIL_SIZES = (400,)
GALLERY_MAX_ITEMS = 10  # live + in-flight gallery items per talent (rejected/failed don't count)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
