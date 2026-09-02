from __future__ import annotations

import re
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from .api import format_utc, validation_error_code
from .models import Booking, Service, TimeSlot
from .validation import validate_datetime_in_timezone, validate_iana_timezone


OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")


@extend_schema_field(OpenApiTypes.DATETIME)
class ISODateTimeField(serializers.CharField):
    pass


def parse_explicit_datetime(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not OFFSET_RE.search(value):
        raise serializers.ValidationError(
            serializers.ErrorDetail(
                "Datetime must include an explicit numeric offset.",
                code="timezone_offset_required",
            )
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise serializers.ValidationError(
            serializers.ErrorDetail(
                f"{field_name} must be a valid ISO 8601 datetime.",
                code="invalid_datetime",
            )
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise serializers.ValidationError(
            serializers.ErrorDetail(
                "Datetime must include an explicit numeric offset.",
                code="timezone_offset_required",
            )
        )
    return parsed


class ServiceSerializer(serializers.ModelSerializer):
    timezone = serializers.CharField(
        source="owner.timezone",
        read_only=True,
        help_text="Owning Provider's IANA timezone name.",
    )

    class Meta:
        model = Service
        fields = ("id", "name", "description", "timezone")
        read_only_fields = ("id", "timezone")
        extra_kwargs = {"name": {"allow_blank": True}}

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError(
                serializers.ErrorDetail(
                    "Service name must be nonblank.", code="service_name_required"
                )
            )
        return value


class PublicServiceSerializer(serializers.ModelSerializer):
    timezone = serializers.CharField(
        source="owner.timezone",
        read_only=True,
        help_text="Owning Provider's IANA timezone name.",
    )

    class Meta:
        model = Service
        fields = ("id", "name", "description", "timezone")


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "UTC serialized slot",
            value={
                "id": 1,
                "service_id": 1,
                "starts_at": "2027-01-15T08:00:00Z",
                "ends_at": "2027-01-15T08:30:00Z",
                "timezone": "Europe/Tallinn",
                "availability": "available",
            },
            response_only=True,
        ),
        OpenApiExample(
            "DST repeated wall time input",
            value={
                "service_id": 1,
                "starts_at": "2027-10-31T03:30:00+03:00",
                "ends_at": "2027-10-31T03:50:00+03:00",
            },
            request_only=True,
        ),
    ]
)
class TimeSlotSerializer(serializers.ModelSerializer):
    service_id = serializers.IntegerField(required=True)
    starts_at = ISODateTimeField(
        help_text="ISO 8601 datetime with an explicit offset; responses use UTC Z."
    )
    ends_at = ISODateTimeField(
        help_text="ISO 8601 datetime with an explicit offset; responses use UTC Z."
    )
    timezone = serializers.CharField(
        source="service.owner.timezone",
        read_only=True,
        help_text="Owning Provider's IANA timezone name.",
    )
    availability = serializers.SerializerMethodField()

    class Meta:
        model = TimeSlot
        fields = ("id", "service_id", "starts_at", "ends_at", "timezone", "availability")
        read_only_fields = ("id", "timezone", "availability")

    def validate(self, attrs):
        request = self.context["request"]
        service_id = attrs.get("service_id", getattr(self.instance, "service_id", None))
        try:
            service = Service.objects.select_related("owner").get(
                pk=service_id, owner=request.user
            )
        except Service.DoesNotExist as exc:
            raise serializers.ValidationError(
                serializers.ErrorDetail(
                    "Owned service was not found.", code="service_not_found"
                )
            ) from exc
        attrs["service"] = service
        for field in ("starts_at", "ends_at"):
            if field in attrs:
                parsed = parse_explicit_datetime(attrs[field], field)
                try:
                    validate_datetime_in_timezone(
                        parsed, service.owner.timezone, field
                    )
                except DjangoValidationError as exc:
                    raise serializers.ValidationError(
                        serializers.ErrorDetail(
                            str(exc), code=validation_error_code(exc)
                        )
                    ) from exc
                attrs[field] = parsed
        if self.instance is not None:
            local_zone = ZoneInfo(service.owner.timezone)
            attrs.setdefault(
                "starts_at", self.instance.starts_at.astimezone(local_zone)
            )
            attrs.setdefault("ends_at", self.instance.ends_at.astimezone(local_zone))
        starts_at = attrs["starts_at"]
        ends_at = attrs["ends_at"]
        if ends_at.astimezone(dt_timezone.utc) <= starts_at.astimezone(dt_timezone.utc):
            raise serializers.ValidationError(
                serializers.ErrorDetail(
                    "Slot must have a positive duration.", code="invalid_interval"
                )
            )
        if starts_at.astimezone(dt_timezone.utc) <= timezone.now():
            raise serializers.ValidationError(
                serializers.ErrorDetail(
                    "Slot must start in the future.", code="slot_not_future"
                )
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("service_id", None)
        return TimeSlot.objects.create(**validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("service_id", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["service_id"] = instance.service_id
        data["starts_at"] = format_utc(instance.starts_at)
        data["ends_at"] = format_utc(instance.ends_at)
        return data

    @extend_schema_field(serializers.ChoiceField(choices=("available", "booked")))
    def get_availability(self, instance):
        return "booked" if hasattr(instance, "booking") else "available"


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "UTC serialized booking",
            value={
                "id": 1,
                "slot_id": 1,
                "service_id": 1,
                "timezone": "Europe/Tallinn",
                "starts_at": "2027-01-15T08:00:00Z",
                "ends_at": "2027-01-15T08:30:00Z",
                "status": "confirmed",
                "created_at": "2027-01-01T10:00:00Z",
            },
            response_only=True,
        )
    ]
)
class BookingSerializer(serializers.ModelSerializer):
    slot_id = serializers.IntegerField(source="slot.id", read_only=True)
    service_id = serializers.IntegerField(source="slot.service_id", read_only=True)
    timezone = serializers.CharField(
        source="slot.service.owner.timezone",
        read_only=True,
        help_text="Owning Provider's IANA timezone name.",
    )
    starts_at = serializers.SerializerMethodField()
    ends_at = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    status = serializers.ChoiceField(
        choices=Booking.Status.choices,
        read_only=True,
    )

    class Meta:
        model = Booking
        fields = (
            "id",
            "slot_id",
            "service_id",
            "timezone",
            "starts_at",
            "ends_at",
            "status",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.DATETIME)
    def get_starts_at(self, instance):
        return format_utc(instance.slot.starts_at)

    @extend_schema_field(OpenApiTypes.DATETIME)
    def get_ends_at(self, instance):
        return format_utc(instance.slot.ends_at)

    @extend_schema_field(OpenApiTypes.DATETIME)
    def get_created_at(self, instance):
        return format_utc(instance.created_at)
