"""Talent serializers.

Privacy: `full_name`, `gender`, `date_of_birth`, email and phone are searchable but must never
reach a public response. Public output is a whitelist of fields on `PublicTalentSerializer`;
`PrivateTalentSerializer` (the owner's own view) extends that whitelist explicitly.
"""

from rest_framework import serializers

from .models import AvailabilityStatus, Experience, TalentCraft, TalentProfile
from .selectors import profile_completeness


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


class PublicTalentSerializer(serializers.ModelSerializer):
    city = CityOutSerializer(read_only=True)
    avatar_id = serializers.UUIDField(read_only=True)
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
            "crafts",
            "experiences",
        )


class CompletenessSerializer(serializers.Serializer):
    percent = serializers.IntegerField()
    missing = serializers.ListField(child=serializers.CharField())
    blocking = serializers.ListField(child=serializers.CharField())
    can_publish = serializers.BooleanField()


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
    bio = serializers.CharField(max_length=2000, allow_blank=True)
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


class AvatarSerializer(serializers.Serializer):
    media_id = serializers.UUIDField(allow_null=True)


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
