from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=(
            "authentication_required",
            "request_error",
            "validation_error",
            "field_not_writable",
            "invalid_timezone",
            "timezone_change_conflict",
            "service_name_required",
            "service_not_found",
            "service_has_slots",
            "timezone_offset_required",
            "invalid_datetime",
            "timezone_offset_mismatch",
            "nonexistent_local_time",
            "invalid_interval",
            "slot_not_future",
            "slot_already_booked",
            "slot_id_required",
            "slot_not_found",
            "no_active_account",
            "token_not_valid",
            "permission_denied",
            "method_not_allowed",
        ),
        help_text="Stable machine-readable error code.",
    )
    message = serializers.CharField(help_text="Human-readable error message.")
    details = serializers.JSONField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()


class ProviderProfileSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=("Provider",), read_only=True)
    timezone = serializers.CharField(
        help_text="Named IANA timezone, for example Europe/Tallinn."
    )


class ProviderProfilePatchSerializer(serializers.Serializer):
    timezone = serializers.CharField(
        help_text="Named IANA timezone, for example Europe/Tallinn."
    )


class ServiceCreateSerializer(serializers.Serializer):
    name = serializers.CharField(
        min_length=1,
        max_length=200,
        help_text="Required nonblank service name.",
    )
    description = serializers.CharField(required=False, allow_blank=True)


class ServicePatchSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)


class BookingCreateSerializer(serializers.Serializer):
    slot_id = serializers.IntegerField(
        min_value=1,
        help_text="Identifier of an available future TimeSlot.",
    )


class TokenPairResponseSerializer(serializers.Serializer):
    access = serializers.CharField(
        help_text="Short-lived JWT access token.",
        read_only=True,
    )
    refresh = serializers.CharField(
        help_text="JWT refresh token.",
        read_only=True,
    )


class TokenObtainRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        style={"input_type": "password"},
        write_only=True,
    )


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="JWT refresh token.")


class TokenAccessResponseSerializer(serializers.Serializer):
    access = serializers.CharField(
        help_text="New short-lived JWT access token.",
        read_only=True,
    )


class HealthResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("ready", "not_ready"))
