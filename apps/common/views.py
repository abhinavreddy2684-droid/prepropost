from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def liveness(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    """Fails (503) if a hard dependency is unreachable - used by the orchestrator."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        cache.set("healthcheck", "1", 5)
        checks["cache"] = "ok" if cache.get("healthcheck") == "1" else "down"
    except Exception:
        checks["cache"] = "down"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse(checks, status=200 if healthy else 503)
