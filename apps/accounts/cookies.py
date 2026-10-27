"""The refresh-token cookie. This is the only module that sets, reads or clears it.

HTTP only: services hand the refresh token back as a string and views decide where it goes.
"""

from django.conf import settings


def set_refresh_cookie(response, refresh: str):
    response.set_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        refresh,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=settings.AUTH_REFRESH_COOKIE_DOMAIN or None,
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )
    return response


def clear_refresh_cookie(response):
    # Path, domain and samesite must match the original cookie or the browser keeps it.
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=settings.AUTH_REFRESH_COOKIE_DOMAIN or None,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )
    return response


def read_refresh_cookie(request) -> str:
    return request.COOKIES.get(settings.AUTH_REFRESH_COOKIE_NAME, "")
