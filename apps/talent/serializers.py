"""Talent serializers.

Privacy: `full_name`, `gender`, `date_of_birth`, email and phone are searchable but must never
reach a public response. Public output is a whitelist of fields on `PublicTalentSerializer`;
`PrivateTalentSerializer` (the owner's own view) extends that whitelist explicitly.
"""

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.media_library.models import Media
from apps.media_library.selectors import signed_urls
from apps.media_library.serializers import SignedUploadSerializer, SignedUrlsSerializer

from .models import (
    AvailabilityStatus,
    AvatarSubmission,
    Experience,
    GalleryItem,
    TalentCraft,
    TalentProfile,
)
from .selectors import (
    CompletenessItem,
    GalleryStage,
    TalentFilters,
    avatar_stage,
    gallery_stage,
    profile_completeness,
)

_MAX_GENRES = 10
_MAX_GENRE_LENGTH = 40  # TalentProfile.genres item length


class CraftRefSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()


class CityOutSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    state_slug = serializers.CharField(source="state.slug")
    state_name = serializers.CharField(source="state.name")


class TalentCraftOutSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(source="craft.slug")
    title = serializers.CharField(source="craft.title")

    class Meta:
        model = TalentCraft
        fields = ("slug", "title", "is_primary")


class ExperienceSerializer(serializers.ModelSerializer):
    craft = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Experience
        fields = ("id", "title", "company", "start_year", "end_year", "description", "craft")


def _urls(media) -> dict | None:
    """Signed URLs. Callers only serialise media the viewer may see (public, owner or staff)."""
    return None if media is None else signed_urls(media)


class PublicTalentSerializer(serializers.ModelSerializer):
    city = CityOutSerializer(read_only=True)
    avatar_id = serializers.UUIDField(read_only=True)
    avatar = serializers.SerializerMethodField()
    crafts = TalentCraftOutSerializer(source="talent_crafts", many=True, read_only=True)
    experiences = ExperienceSerializer(many=True, read_only=True)

    class Meta:
        model = TalentProfile
        fields = (
            "id",
            "professional_name",
            "bio",
            "city",
            "years_experience",
            "genres",
            "avatar_id",
            "avatar",
            "crafts",
            "experiences",
        )

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_avatar(self, obj) -> dict | None:
        """The approved photo only; a photo in review is never public."""
        return _urls(obj.avatar.media) if obj.avatar_id else None


class TalentCardSerializer(serializers.ModelSerializer):
    """A search result. A strict whitelist: includes the full bio, never experiences or any private
    field (full_name, gender, date_of_birth, email, phone)."""

    city = CityOutSerializer(read_only=True)
    primary_craft = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    is_available = serializers.BooleanField(
        read_only=True, help_text="False only if every one of the next 7 days is unavailable."
    )

    class Meta:
        model = TalentProfile
        fields = (
            "id",
            "professional_name",
            "bio",
            "city",
            "years_experience",
            "genres",
            "primary_craft",
            "avatar",
            "is_available",
        )

    @extend_schema_field(CraftRefSerializer(allow_null=True))
    def get_primary_craft(self, obj) -> dict | None:
        # Reads the prefetched crafts; filtering in Python avoids a query per card.
        for talent_craft in obj.talent_crafts.all():
            if talent_craft.is_primary:
                return {"slug": talent_craft.craft.slug, "title": talent_craft.craft.title}
        return None

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_avatar(self, obj) -> dict | None:
        """The approved photo only (`avatar` is set only by approval)."""
        return _urls(obj.avatar.media) if obj.avatar_id else None


class TalentSearchParamsSerializer(serializers.Serializer):
    """Query parameters of GET /api/talent. Unknown parameters (cursor, limit) are ignored."""

    q = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text="Case-insensitive match on professional name, bio or city name.",
    )
    craft = serializers.SlugField(
        required=False, max_length=140, help_text="Craft slug. Unknown slugs match nobody."
    )
    primary_craft_only = serializers.BooleanField(
        required=False, help_text="With `craft`: only talent whose primary craft it is."
    )
    state = serializers.SlugField(required=False, max_length=140, help_text="State slug.")
    city = serializers.SlugField(
        required=False,
        max_length=140,
        help_text="City slug. Slugs are unique only within a state; add `state` to be exact.",
    )
    genres = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=_MAX_GENRES * 41,
        help_text=f"Comma-separated, at most {_MAX_GENRES}. Matches talent with any of them.",
    )
    available = serializers.BooleanField(
        required=False,
        help_text="true: not fully booked over the next 7 days. false: fully booked.",
    )
    # Restricted: approved recruiters only (401 anonymous, 403 anyone else). Never sent by the
    # public listing.
    gender = serializers.ChoiceField(
        choices=TalentProfile.Gender.choices,
        required=False,
        help_text="RESTRICTED to approved recruiters.",
    )
    min_age = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=120,
        help_text="Years, inclusive. RESTRICTED to approved recruiters.",
    )
    max_age = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=120,
        help_text="Years, inclusive. RESTRICTED to approved recruiters.",
    )

    def validate_genres(self, value: str) -> tuple[str, ...]:
        genres = tuple(dict.fromkeys(g.strip() for g in value.split(",") if g.strip()))
        if len(genres) > _MAX_GENRES or any(len(g) > _MAX_GENRE_LENGTH for g in genres):
            raise serializers.ValidationError(
                f"Send at most {_MAX_GENRES} genres of {_MAX_GENRE_LENGTH} characters or fewer."
            )
        return genres

    def validate(self, attrs):
        low, high = attrs.get("min_age"), attrs.get("max_age")
        if low is not None and high is not None and low > high:
            raise serializers.ValidationError({"min_age": "Must not be greater than max_age."})
        return attrs

    def to_filters(self) -> TalentFilters:
        data = self.validated_data
        return TalentFilters(
            q=data.get("q", "").strip(),
            craft_slug=data.get("craft", ""),
            primary_craft_only=data.get("primary_craft_only", False),
            state_slug=data.get("state", ""),
            city_slug=data.get("city", ""),
            gender=data.get("gender", ""),
            min_age=data.get("min_age"),
            max_age=data.get("max_age"),
            available=data.get("available"),
            genres=data.get("genres", ()),
        )


class NextStepSerializer(serializers.Serializer):
    item = serializers.ChoiceField(choices=CompletenessItem.choices)
    label = serializers.CharField()
    hint = serializers.CharField(help_text="What to do next, ready to show to the talent.")
    weight = serializers.IntegerField(help_text="Share of the percentage this item is worth.")
    blocking = serializers.BooleanField(help_text="Required before the profile can publish.")


class CompletenessSerializer(serializers.Serializer):
    percent = serializers.IntegerField(help_text="Weighted; 100 when nothing is missing.")
    missing = serializers.ListField(child=serializers.CharField())
    blocking = serializers.ListField(child=serializers.CharField())
    can_publish = serializers.BooleanField()
    next_steps = NextStepSerializer(
        many=True, help_text="Missing items, blocking ones first, then the most valuable."
    )


class PrivateTalentSerializer(PublicTalentSerializer):
    onboarding_completed = serializers.SerializerMethodField()
    completeness = serializers.SerializerMethodField()

    class Meta(PublicTalentSerializer.Meta):
        fields = (
            *PublicTalentSerializer.Meta.fields,
            "full_name",
            "gender",
            "date_of_birth",
            "is_published",
            "kyc_status",
            "onboarding_completed",
            "completeness",
        )

    def get_onboarding_completed(self, obj) -> bool:
        return obj.onboarding_completed_at is not None

    def get_completeness(self, obj) -> dict:
        return CompletenessSerializer(profile_completeness(obj)).data


# --- input -------------------------------------------------------------------


class TalentUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    professional_name = serializers.CharField(max_length=150)
    bio = serializers.CharField(max_length=settings.TALENT_BIO_MAX_CHARS, allow_blank=True)
    city_id = serializers.UUIDField(allow_null=True)
    years_experience = serializers.IntegerField(allow_null=True)
    genres = serializers.ListField(child=serializers.CharField(max_length=40), max_length=20)
    gender = serializers.CharField(max_length=12, allow_blank=True)
    date_of_birth = serializers.DateField(allow_null=True)


class TalentCreateSerializer(TalentUpdateSerializer):
    """Same fields; only the names are mandatory when the profile is first created."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.required = name in ("full_name", "professional_name")


class CraftsSerializer(serializers.Serializer):
    primary = serializers.SlugField()
    supporting = serializers.ListField(child=serializers.SlugField(), required=False, default=list)


class PublishSerializer(serializers.Serializer):
    published = serializers.BooleanField()


class ExperienceWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=160)
    company = serializers.CharField(max_length=160, allow_blank=True)
    start_year = serializers.IntegerField()
    end_year = serializers.IntegerField(allow_null=True)
    description = serializers.CharField(max_length=2000, allow_blank=True)
    craft = serializers.SlugField(allow_null=True)


class ExperienceCreateSerializer(ExperienceWriteSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.required = name in ("title", "start_year")


class AvailabilityEntrySerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=AvailabilityStatus.choices)


class AvailabilityWriteSerializer(serializers.Serializer):
    entries = AvailabilityEntrySerializer(many=True, allow_empty=False, max_length=366)


class AvailabilityQuerySerializer(serializers.Serializer):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)


# --- gallery and avatar ------------------------------------------------------------------------


class GalleryItemOwnerSerializer(serializers.ModelSerializer):
    """The owner's view of an item: everything, plus the derived stage. Polled during upload."""

    kind = serializers.SerializerMethodField()
    media_id = serializers.UUIDField(read_only=True, allow_null=True)
    craft = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    stage = serializers.SerializerMethodField()
    failure_reason = serializers.SerializerMethodField()
    urls = serializers.SerializerMethodField()

    class Meta:
        model = GalleryItem
        fields = (
            "id",
            "kind",
            "media_id",
            "external_url",
            "link_provider",
            "title",
            "description",
            "year",
            "craft",
            "position",
            "stage",
            "failure_reason",
            "moderation_reason",
            "moderation_note",
            "submitted_at",
            "likes_count",
            "urls",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_kind(self, obj) -> str:
        return "link" if obj.is_link else obj.media.kind

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_urls(self, obj) -> dict | None:
        return _urls(obj.media)

    @extend_schema_field(serializers.ChoiceField(choices=GalleryStage.choices))
    def get_stage(self, obj) -> str:
        return gallery_stage(obj)

    @extend_schema_field(
        serializers.ChoiceField(choices=Media.FailureReason.choices, allow_blank=True)
    )
    def get_failure_reason(self, obj) -> str:
        return "" if obj.is_link else obj.media.failure_reason


class _GalleryFieldsSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=160, allow_blank=True, required=False)
    description = serializers.CharField(max_length=2000, allow_blank=True, required=False)
    year = serializers.IntegerField(allow_null=True, required=False)
    craft = serializers.SlugField(allow_null=True, required=False, help_text="A craft slug.")


class GalleryUploadSerializer(_GalleryFieldsSerializer):
    content_type = serializers.CharField(max_length=100)
    size = serializers.IntegerField(min_value=1, help_text="File size in bytes.")


class GalleryLinkSerializer(_GalleryFieldsSerializer):
    external_url = serializers.URLField(max_length=1000)
    title = serializers.CharField(max_length=160)


class GalleryItemUpdateSerializer(_GalleryFieldsSerializer):
    position = serializers.IntegerField(min_value=0, required=False)


class GalleryUploadResponseSerializer(serializers.Serializer):
    item = GalleryItemOwnerSerializer()
    media_id = serializers.UUIDField()
    upload = SignedUploadSerializer()
    expires_at = serializers.DateTimeField()


class AvatarUploadSerializer(serializers.Serializer):
    content_type = serializers.CharField(max_length=100)
    size = serializers.IntegerField(min_value=1, help_text="File size in bytes.")


class AvatarSubmissionOwnerSerializer(serializers.ModelSerializer):
    media_id = serializers.UUIDField(read_only=True)
    stage = serializers.SerializerMethodField()
    failure_reason = serializers.CharField(source="media.failure_reason", read_only=True)
    urls = serializers.SerializerMethodField()

    class Meta:
        model = AvatarSubmission
        fields = (
            "id",
            "media_id",
            "stage",
            "failure_reason",
            "moderation_reason",
            "moderation_note",
            "urls",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=GalleryStage.choices))
    def get_stage(self, obj) -> str:
        return avatar_stage(obj)

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_urls(self, obj) -> dict | None:
        return _urls(obj.media)  # owner only


class ApprovedAvatarSerializer(serializers.ModelSerializer):
    media_id = serializers.UUIDField(read_only=True)
    approved_at = serializers.DateTimeField(source="reviewed_at", read_only=True)
    urls = serializers.SerializerMethodField()

    class Meta:
        model = AvatarSubmission
        fields = ("id", "media_id", "approved_at", "urls")
        read_only_fields = fields

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_urls(self, obj) -> dict | None:
        return _urls(obj.media)


class AvatarStateSerializer(serializers.Serializer):
    avatar = ApprovedAvatarSerializer(allow_null=True, help_text="The current approved photo.")
    submission = AvatarSubmissionOwnerSerializer(
        allow_null=True, help_text="A newer photo on its way: uploading, in review, or refused."
    )


class AvatarUploadResponseSerializer(serializers.Serializer):
    submission = AvatarSubmissionOwnerSerializer()
    media_id = serializers.UUIDField()
    upload = SignedUploadSerializer()
    expires_at = serializers.DateTimeField()


class PublicGalleryItemSerializer(serializers.ModelSerializer):
    """What a visitor sees. Only items passing the public visibility rule reach this."""

    kind = serializers.SerializerMethodField()
    craft = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    width = serializers.IntegerField(source="media.width", read_only=True, allow_null=True)
    height = serializers.IntegerField(source="media.height", read_only=True, allow_null=True)
    urls = serializers.SerializerMethodField()

    class Meta:
        model = GalleryItem
        fields = (
            "id",
            "kind",
            "external_url",
            "link_provider",
            "title",
            "description",
            "year",
            "craft",
            "position",
            "likes_count",
            "width",
            "height",
            "urls",
        )
        read_only_fields = fields

    def get_kind(self, obj) -> str:
        return "link" if obj.is_link else obj.media.kind

    @extend_schema_field(SignedUrlsSerializer(allow_null=True))
    def get_urls(self, obj) -> dict | None:
        return _urls(obj.media)


class LikeStateSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    liked = serializers.BooleanField()
    likes_count = serializers.IntegerField()
