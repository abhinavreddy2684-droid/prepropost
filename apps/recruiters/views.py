from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified
from apps.common.exceptions import ValidationFailed
from apps.common.serializers import ErrorEnvelopeSerializer
from apps.reference import selectors as reference_selectors

from . import selectors, serializers, services
from .permissions import IsRecruiter

_ERROR = OpenApiResponse(ErrorEnvelopeSerializer)


def _fields(validated: dict) -> dict:
    """Translate the `city_id` the client sends into the `city` the service expects."""
    data = dict(validated)
    if "city_id" in data:
        city_id = data.pop("city_id")
        city = None if city_id is None else reference_selectors.get_active_city(city_id)
        if city_id is not None and city is None:
            raise ValidationFailed("Unknown city.", details={"field": "city_id"})
        data["city"] = city
    return data


def _out(profile) -> dict:
    return serializers.RecruiterProfileSerializer(
        selectors.get_recruiter_profile(profile.user)
    ).data


class RecruiterMeView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":  # creating the profile: any signed-in account
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsRecruiter()]

    @extend_schema(
        summary="Your recruiter profile, including verification status",
        responses={200: serializers.RecruiterProfileSerializer, 401: _ERROR, 403: _ERROR},
    )
    def get(self, request):
        return Response(_out(selectors.get_recruiter_profile(request.user)))

    @extend_schema(
        summary="Create your recruiter profile (starts unverified)",
        request=serializers.RecruiterCreateSerializer,
        responses={
            201: serializers.RecruiterProfileSerializer,
            400: _ERROR,
            401: _ERROR,
            409: _ERROR,
        },
    )
    def post(self, request):
        data = serializers.RecruiterCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile = services.create_recruiter_profile(
            user=request.user, **_fields(data.validated_data)
        )
        return Response(_out(profile), status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Update your profile (send only the fields to change)",
        request=serializers.RecruiterUpdateSerializer,
        responses={
            200: serializers.RecruiterProfileSerializer,
            400: _ERROR,
            401: _ERROR,
            403: _ERROR,
        },
    )
    def patch(self, request):
        data = serializers.RecruiterUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        profile = services.update_recruiter_profile(
            profile=selectors.get_recruiter_profile(request.user), data=_fields(data.validated_data)
        )
        return Response(_out(profile))


class RecruiterSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsRecruiter, IsEmailVerified]

    @extend_schema(
        summary="Submit your profile for manual review (needs a verified email)",
        request=None,
        responses={
            200: serializers.RecruiterProfileSerializer,
            401: _ERROR,
            403: _ERROR,
            409: _ERROR,
        },
    )
    def post(self, request):
        profile = services.submit_for_verification(
            profile=selectors.get_recruiter_profile(request.user)
        )
        return Response(_out(profile))
