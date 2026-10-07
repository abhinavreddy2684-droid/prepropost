"""Recruiter serializers.

`verified_by` is deliberately absent: reviewers are an internal detail, not for the recruiter.
"""

from rest_framework import serializers

from .models import RecruiterProfile


class RecruiterCityOutSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    state_slug = serializers.CharField(source="state.slug")
    state_name = serializers.CharField(source="state.name")


class RecruiterProfileSerializer(serializers.ModelSerializer):
    city = RecruiterCityOutSerializer(allow_null=True)

    class Meta:
        model = RecruiterProfile
        fields = (
            "id",
            "display_name",
            "company_name",
            "recruiter_type",
            "website",
            "city",
            "verification_status",
            "rejection_reason",
            "verified_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class RecruiterUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=150)
    company_name = serializers.CharField(max_length=200, allow_blank=True)
    recruiter_type = serializers.CharField(max_length=20)
    website = serializers.URLField(max_length=200, allow_blank=True)
    city_id = serializers.UUIDField(allow_null=True)


class RecruiterCreateSerializer(RecruiterUpdateSerializer):
    """Same fields; only the display name is mandatory when the profile is first created."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.required = name == "display_name"
