from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    # Validation lives in the service so there is one source of truth.
    email = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)


class LoginSerializer(RegisterSerializer):
    pass


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    email_verified = serializers.BooleanField()
    roles = serializers.ListField(child=serializers.CharField())


class RegisteredSerializer(TokenPairSerializer):
    user = MeSerializer()
