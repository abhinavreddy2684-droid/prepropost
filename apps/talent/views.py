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
from apps.media_library.serializers import signed_upload
from apps.media_library.views import UPLOAD_SEQUENCE
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


# --- gallery ---------------------------------------------------------------------------------


def _gallery_fields(validated: dict) -> dict:
    data = dict(validated)
    if "craft" in data:
        slug = data["craft"]
        data["craft"] = _crafts([slug])[slug] if slug else None
    return data


def _only(request, allowed) -> None:
    """The file or link of an item never changes; say so rather than silently ignore it."""
    unknown = sorted(set(request.data) - set(allowed))
    if unknown:
        raise ValidationFailed(
            f"Fields not editable: {', '.join(unknown)}.",
            code="field_not_editable",
            details={"fields": unknown},
        )


def _owner_item(item) -> dict:
    return serializers.GalleryItemOwnerSerializer(item).data


class GalleryListView(_OwnView):
    @extend_schema(
        summary="Your gallery, every stage (deleted items excluded)",
        description="Ordered by `position`. `stage` is one of uploading, processing, "
        "under_review, live, hidden, failed, rejected.",
        responses={200: serializers.GalleryItemOwnerSerializer(many=True)},
    )
    def get(self, request):
        items = selectors.list_own_gallery(_own(request))
        return Response(serializers.GalleryItemOwnerSerializer(items, many=True).data)


class GalleryUploadView(_OwnView):
    @extend_schema(
        summary="Start a gallery file upload",
        description=UPLOAD_SEQUENCE
        + "\nImages (JPEG/PNG/WebP) up to 10 MB, audio (MP3/M4A) 20 MB, video (MP4) 200 MB, "
        "PDF 10 MB. HEIC is not accepted. 409 `gallery_full` at the gallery limit, "
        "409 `too_many_uploads` when too many uploads are still in progress. The item enters "
        "review once its file is processed and it has a title.",
        request=serializers.GalleryUploadSerializer,
        responses={201: serializers.GalleryUploadResponseSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.GalleryUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        item, ticket = services.start_gallery_upload(
            talent=_own(request), **_gallery_fields(data.validated_data)
        )
        payload = {
            "item": _owner_item(selectors.get_own_gallery_item(item.talent, item.pk)),
            "media_id": ticket.media.id,
            "upload": signed_upload(ticket),
            "expires_at": ticket.expires_at,
        }
        return Response(payload, status=status.HTTP_201_CREATED)


class GalleryLinkView(_OwnView):
    @extend_schema(
        summary="Add a link (YouTube, Vimeo, SoundCloud ...) to your gallery",
        description="https links only. Goes straight to review. 409 `gallery_full` at the limit.",
        request=serializers.GalleryLinkSerializer,
        responses={201: serializers.GalleryItemOwnerSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.GalleryLinkSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        item = services.add_gallery_link(
            talent=_own(request), **_gallery_fields(data.validated_data)
        )
        return Response(_owner_item(item), status=status.HTTP_201_CREATED)


class GalleryItemView(_OwnView):
    def _item(self, request, pk):
        item = selectors.get_own_gallery_item(_own(request), pk)
        if item is None:
            raise NotFound("Gallery item not found.")
        return item

    @extend_schema(
        summary="One of your gallery items (poll this during upload)",
        responses={200: serializers.GalleryItemOwnerSerializer, 404: _ERROR},
    )
    def get(self, request, pk):
        return Response(_owner_item(self._item(request, pk)))

    @extend_schema(
        summary="Edit an item's text, craft or position",
        description="The file and the link cannot change: delete the item and add a new one. "
        "Edits to a live item stay live. Rejected items cannot be edited (409 `item_rejected`).",
        request=serializers.GalleryItemUpdateSerializer,
        responses={
            200: serializers.GalleryItemOwnerSerializer,
            400: _ERROR,
            404: _ERROR,
            409: _ERROR,
        },
    )
    def patch(self, request, pk):
        _only(request, serializers.GalleryItemUpdateSerializer().fields)
        data = serializers.GalleryItemUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        talent = _own(request)
        services.update_gallery_item(
            talent=talent, item_id=pk, data=_gallery_fields(data.validated_data)
        )
        return Response(_owner_item(self._item(request, pk)))

    @extend_schema(summary="Delete a gallery item", responses={204: None, 404: _ERROR})
    def delete(self, request, pk):
        services.delete_gallery_item(talent=_own(request), item_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- avatar ----------------------------------------------------------------------------------


def _avatar_state(talent) -> dict:
    talent = selectors.load_profile(talent.pk)
    return serializers.AvatarStateSerializer(
        {"avatar": talent.avatar, "submission": selectors.latest_avatar_submission(talent)}
    ).data


class AvatarView(_OwnView):
    @extend_schema(
        summary="Your approved profile photo and any newer one in progress",
        description="The approved photo stays visible while a new one is uploaded and reviewed.",
        responses={200: serializers.AvatarStateSerializer},
    )
    def get(self, request):
        return Response(_avatar_state(_own(request)))


class AvatarUploadView(_OwnView):
    @extend_schema(
        summary="Start uploading a new profile photo",
        description=UPLOAD_SEQUENCE
        + "\nJPEG/PNG/WebP up to 10 MB. 409 `avatar_submission_open` while another new photo "
        "is still uploading, processing or in review (withdraw it first).",
        request=serializers.AvatarUploadSerializer,
        responses={201: serializers.AvatarUploadResponseSerializer, 400: _ERROR, 409: _ERROR},
    )
    def post(self, request):
        data = serializers.AvatarUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        submission, ticket = services.start_avatar_upload(
            talent=_own(request), **data.validated_data
        )
        payload = {
            "submission": serializers.AvatarSubmissionOwnerSerializer(submission).data,
            "media_id": ticket.media.id,
            "upload": signed_upload(ticket),
            "expires_at": ticket.expires_at,
        }
        return Response(payload, status=status.HTTP_201_CREATED)


class AvatarSubmissionView(_OwnView):
    @extend_schema(
        summary="Withdraw the new profile photo that is uploading or in review",
        responses={204: None, 404: _ERROR},
    )
    def delete(self, request):
        services.withdraw_avatar_submission(talent=_own(request))
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


class PublicGalleryView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="A published talent's public gallery",
        description="Approved links and approved, processed files only, by position. File URLs "
        "are signed and short-lived (`urls.expires_at`); fetch again for fresh ones.",
        responses={200: serializers.PublicGalleryItemSerializer(many=True), 404: _ERROR},
    )
    def get(self, request, pk):
        if selectors.get_public_talent(pk) is None:
            raise NotFound("Talent not found.")
        items = selectors.public_gallery_items(pk)
        return Response(serializers.PublicGalleryItemSerializer(items, many=True).data)


class GalleryItemLikeView(APIView):
    permission_classes = [IsAuthenticated]

    def _state(self, item, liked: bool) -> Response:
        return Response({"item_id": item.pk, "liked": liked, "likes_count": item.likes_count})

    @extend_schema(
        summary="Like a public gallery item (idempotent)",
        request=None,
        responses={200: serializers.LikeStateSerializer, 401: _ERROR, 404: _ERROR},
    )
    def post(self, request, pk):
        return self._state(services.like_item(user=request.user, item_id=pk), True)

    @extend_schema(
        summary="Remove your like (idempotent)",
        request=None,
        responses={200: serializers.LikeStateSerializer, 401: _ERROR, 404: _ERROR},
    )
    def delete(self, request, pk):
        return self._state(services.unlike_item(user=request.user, item_id=pk), False)
