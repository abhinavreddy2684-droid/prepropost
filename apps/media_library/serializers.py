from rest_framework import serializers

from .models import Media


class MediaStateSerializer(serializers.ModelSerializer):
    """The byte lifecycle as the uploader sees it."""

    class Meta:
        model = Media
        fields = (
            "id",
            "purpose",
            "kind",
            "status",
            "failure_reason",
            "declared_content_type",
            "declared_size",
            "actual_size",
            "width",
            "height",
            "expires_at",
            "uploaded_at",
            "ready_at",
        )
        read_only_fields = fields


class SignedUploadSerializer(serializers.Serializer):
    """POST `fields` (in order) then the file as `file`, multipart/form-data, to `url`."""

    url = serializers.URLField()
    fields = serializers.DictField(child=serializers.CharField())
    expires_at = serializers.DateTimeField(help_text="The signed form stops working after this.")


def signed_upload(ticket) -> dict:
    return {
        "url": ticket.upload.url,
        "fields": ticket.upload.fields,
        "expires_at": ticket.upload_expires_at,
    }


class UploadTicketSerializer(serializers.Serializer):
    media_id = serializers.UUIDField()
    upload = SignedUploadSerializer()
    expires_at = serializers.DateTimeField(
        help_text="Confirm before this or the upload expires and must be started again."
    )


def ticket_payload(ticket) -> dict:
    return {
        "media_id": ticket.media.id,
        "upload": signed_upload(ticket),
        "expires_at": ticket.expires_at,
    }


class SignedUrlsSerializer(serializers.Serializer):
    """Short-lived GET URLs. Refetch the owning resource for fresh ones after `expires_at`."""

    original = serializers.URLField()
    thumbnails = serializers.DictField(
        child=serializers.URLField(), help_text="WebP thumbnails by longest edge in pixels."
    )
    expires_at = serializers.DateTimeField()
