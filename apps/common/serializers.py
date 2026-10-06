"""Schema-only serializers describing the shared error envelope for OpenAPI."""

from rest_framework import serializers


class _ErrorBody(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField()


class ErrorEnvelopeSerializer(serializers.Serializer):
    error = _ErrorBody()
