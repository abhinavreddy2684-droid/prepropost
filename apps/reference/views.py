from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from django.views.decorators.http import condition
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import get_bootstrap


def _etag(request, *args, **kwargs):
    return get_bootstrap().version


@method_decorator(condition(etag_func=_etag), name="get")
@method_decorator(
    cache_control(public=True, max_age=settings.BOOTSTRAP_CLIENT_MAX_AGE_SECONDS), name="get"
)
class BootstrapView(APIView):
    """Public, user-agnostic reference data. Safe for any cache or CDN to store."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(summary="Reference data: crafts, locations and enums", responses=dict)
    def get(self, request):
        return Response(get_bootstrap().payload)
