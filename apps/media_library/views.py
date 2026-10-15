from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.serializers import ErrorEnvelopeSerializer

from . import serializers, services

_ERROR = OpenApiResponse(ErrorEnvelopeSerializer)

UPLOAD_SEQUENCE = """
**Upload sequence** (every media purpose):

1. Call the domain upload endpoint (e.g. `POST /api/talent/me/gallery/uploads`). It validates
   type, size and quotas and returns `upload` (`url` + `fields`) and `expires_at`.
2. POST the file to `upload.url` as `multipart/form-data`: every entry of `upload.fields` first,
   then the file as `file`. The bucket enforces the key, the exact `Content-Type` and the size.
3. When the bucket answers 2xx, call `POST /api/media/{media_id}/confirm` straight away (no user
   action). If the signed form expired first, get a new one from `upload-url`.
4. A worker validates the real file, strips metadata and makes thumbnails.
5. Poll the owning item every 2-3 s for its stage:
   uploading -> processing -> under_review -> live, or failed / rejected.
"""


class _OwnMediaView(APIView):
    permission_classes = [IsAuthenticated]


class MediaConfirmView(_OwnMediaView):
    @extend_schema(
        summary="Confirm that the file reached storage (idempotent)",
        description=UPLOAD_SEQUENCE
        + "\nOnly the uploader may confirm. Already confirmed: returns the current state. "
        "409 `upload_missing`: the file is not in storage yet (retry). "
        "410: the upload expired or was removed.",
        request=None,
        responses={
            200: serializers.MediaStateSerializer,
            404: _ERROR,
            409: _ERROR,
            410: _ERROR,
            503: _ERROR,
        },
    )
    def post(self, request, pk):
        media = services.confirm(user=request.user, media_id=pk)
        return Response(serializers.MediaStateSerializer(media).data)


class MediaCancelView(_OwnMediaView):
    @extend_schema(
        summary="Give up on an upload that has not been confirmed",
        description="Only a pending upload can be cancelled; the item waiting on it is removed.",
        request=None,
        responses={200: serializers.MediaStateSerializer, 404: _ERROR, 409: _ERROR},
    )
    def post(self, request, pk):
        media = services.cancel(user=request.user, media_id=pk)
        return Response(serializers.MediaStateSerializer(media).data)


class MediaUploadUrlView(_OwnMediaView):
    @extend_schema(
        summary="A fresh signed upload form for the same pending upload",
        description="Does not extend `expires_at`. 410 once the upload has expired; 409 once "
        "it has been confirmed.",
        request=None,
        responses={
            200: serializers.UploadTicketSerializer,
            404: _ERROR,
            409: _ERROR,
            410: _ERROR,
            503: _ERROR,
        },
    )
    def post(self, request, pk):
        ticket = services.reissue_upload_url(user=request.user, media_id=pk)
        return Response(serializers.ticket_payload(ticket))
