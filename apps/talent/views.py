from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import NotFound, ValidationFailed
from apps.common.serializers import ErrorEnvelopeSerializer
from apps.reference import selectors as reference_selectors

from . import selectors, serializers, services
from .permissions import IsTalent

_ERROR = OpenApiResponse(ErrorEnvelopeSerializer)
_OWN = [IsAuthenticated, IsTalent]


# --- request translation: ids and slugs in, model instances out ------------------------------


def _city(city_id):
    city = reference_selectors.get_active_city(city_id)
    if city is None:
        raise ValidationFailed("Unknown city.", details={"field": "city_id"})
    return city


def _crafts(slugs):
    found = reference_selectors.crafts_by_slug(slugs)
    unknown = sorted(set(slugs) - set(found))
    if unknown:
        raise ValidationFailed(
            f"Unknown craft: {', '.join(unknown)}.", details={"unknown": unknown}
        )
    return found


def _profile_fields(validated: dict) -> dict:
    data = dict(validated)
    if "city_id" in data:
        city_id = data.pop("city_id")
        data["city"] = None if city_id is None else _city(city_id)
    return data


def _experience_fields(validated: dict) -> dict:
    data = dict(validated)
    if data.get("craft"):
        data["craft"] = _crafts([data["craft"]])[data["craft"]]
    return data


def _own(request):
    return selectors.get_talent_profile(request.user)


def _private(talent_id) -> dict:
    return serializers.PrivateTalentSerializer(selectors.load_profile(talent_id)).data


# --- own profile -----------------------------------------------------------------------------


class TalentMeView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":  # creating the profile: any signed-in account
            return [IsAuthenticated()]
        return [permission() for permission in _OWN]

    @extend_schema(
        summary="Your talent profile, including private fields and completeness",
        responses={200: serializers.PrivateTalentSerializer, 401: _ERROR, 403: _ERROR},
    )
    def get(self, request):
        return Response(_private(_own(request).pk))

    @extend_schema(
        summary="Start onboarding: create your talent profile",
        request=serializers.TalentCreateSerializer,
        responses={201: serializers.PrivateTalentSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.TalentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        talent = services.create_talent_profile(
            user=request.user, **_profile_fields(data.validated_data)
        )
        return Response(_private(talent.pk), status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Update your profile (send only the fields to change)",
        request=serializers.TalentUpdateSerializer,
        responses={200: serializers.PrivateTalentSerializer, 400: _ERROR},
    )
    def patch(self, request):
        data = serializers.TalentUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        talent = _own(request)
        services.update_talent_profile(talent=talent, data=_profile_fields(data.validated_data))
        return Response(_private(talent.pk))


class _OwnView(APIView):
    permission_classes = _OWN


class TalentCraftsView(_OwnView):
    @extend_schema(
        summary="Replace your crafts: one primary plus any supporting (by slug)",
        request=serializers.CraftsSerializer,
        responses={200: serializers.PrivateTalentSerializer, 400: _ERROR},
    )
    def put(self, request):
        data = serializers.CraftsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        primary = data.validated_data["primary"]
        supporting = data.validated_data["supporting"]
        found = _crafts({primary, *supporting})
        talent = _own(request)
        services.set_talent_crafts(
            talent=talent,
            primary=found[primary],
            supporting=[found[slug] for slug in supporting],
        )
        return Response(_private(talent.pk))


class TalentAvatarView(_OwnView):
    @extend_schema(
        summary="Set (or clear, with null) your profile photo",
        request=serializers.AvatarSerializer,
        responses={200: serializers.PrivateTalentSerializer, 400: _ERROR},
    )
    def put(self, request):
        data = serializers.AvatarSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        talent = _own(request)
        services.set_avatar(talent=talent, media_id=data.validated_data["media_id"])
        return Response(_private(talent.pk))


class CompleteOnboardingView(_OwnView):
    @extend_schema(
        summary="Finish onboarding; publishes the profile the first time",
        request=None,
        responses={200: serializers.PrivateTalentSerializer, 400: _ERROR},
    )
    def post(self, request):
        talent = _own(request)
        services.complete_onboarding(talent=talent)
        return Response(_private(talent.pk))


class PublishView(_OwnView):
    @extend_schema(
        summary="Show or hide your profile in search",
        request=serializers.PublishSerializer,
        responses={200: serializers.PrivateTalentSerializer, 400: _ERROR},
    )
    def put(self, request):
        data = serializers.PublishSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        talent = _own(request)
        services.set_published(talent=talent, published=data.validated_data["published"])
        return Response(_private(talent.pk))


# --- experience ------------------------------------------------------------------------------


class ExperienceListView(_OwnView):
    @extend_schema(
        summary="Your experience, newest first",
        responses={200: serializers.ExperienceSerializer(many=True)},
    )
    def get(self, request):
        experiences = selectors.list_experiences(_own(request))
        return Response(serializers.ExperienceSerializer(experiences, many=True).data)

    @extend_schema(
        summary="Add an experience entry",
        request=serializers.ExperienceCreateSerializer,
        responses={201: serializers.ExperienceSerializer, 400: _ERROR},
    )
    def post(self, request):
        data = serializers.ExperienceCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        experience = services.add_experience(
            talent=_own(request), **_experience_fields(data.validated_data)
        )
        return Response(
            serializers.ExperienceSerializer(experience).data, status=status.HTTP_201_CREATED
        )


class ExperienceDetailView(_OwnView):
    @extend_schema(
        summary="Update an experience entry",
        request=serializers.ExperienceWriteSerializer,
        responses={200: serializers.ExperienceSerializer, 400: _ERROR, 404: _ERROR},
    )
    def patch(self, request, pk):
        data = serializers.ExperienceWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        experience = services.update_experience(
            talent=_own(request),
            experience_id=pk,
            data=_experience_fields(data.validated_data),
        )
        return Response(serializers.ExperienceSerializer(experience).data)

    @extend_schema(summary="Delete an experience entry", responses={204: None, 404: _ERROR})
    def delete(self, request, pk):
        services.delete_experience(talent=_own(request), experience_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- availability ----------------------------------------------------------------------------


class AvailabilityView(_OwnView):
    @extend_schema(
        summary="Your unavailable/tentative days (anything not listed is available)",
        parameters=[serializers.AvailabilityQuerySerializer],
        responses={200: serializers.AvailabilityEntrySerializer(many=True), 400: _ERROR},
    )
    def get(self, request):
        query = serializers.AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        today = timezone.localdate()
        start = query.validated_data.get("start", today)
        end = query.validated_data.get(
            "end", today + timedelta(days=settings.AVAILABILITY_WINDOW_DAYS)
        )
        if end < start or (end - start).days > 366:
            raise ValidationFailed("Choose a range of at most 366 days, start before end.")
        rows = selectors.availability_overrides_between(_own(request).pk, start, end).order_by(
            "date"
        )
        return Response([{"date": row.date, "status": row.status} for row in rows])

    @extend_schema(
        summary="Set days; 'available' clears a day",
        request=serializers.AvailabilityWriteSerializer,
        responses={204: None, 400: _ERROR},
    )
    def put(self, request):
        data = serializers.AvailabilityWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        entries = {e["date"]: e["status"] for e in data.validated_data["entries"]}
        services.set_availability(talent=_own(request), entries=entries)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- public ----------------------------------------------------------------------------------


class PublicTalentView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="A published talent's public profile",
        responses={200: serializers.PublicTalentSerializer, 404: _ERROR},
    )
    def get(self, request, pk):
        talent = selectors.get_public_talent(pk)
        if talent is None:
            raise NotFound("Talent not found.")
        return Response(serializers.PublicTalentSerializer(talent).data)
