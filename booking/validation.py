from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError


def validate_iana_timezone(value):
    if not value:
        raise ValidationError("A named IANA timezone is required.", code="invalid_timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Timezone must be a valid IANA name.", code="invalid_timezone")


def validate_datetime_in_timezone(value: datetime, timezone_name: str, field_name: str):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(
            {
                field_name: ValidationError(
                    "Datetime must include an explicit numeric offset.",
                    code="timezone_offset_required",
                )
            }
        )
    validate_iana_timezone(timezone_name)
    zone = ZoneInfo(timezone_name)
    wall_time = value.replace(tzinfo=None)
    supplied_offset = value.utcoffset()

    valid_candidates = []
    for fold in (0, 1):
        candidate = wall_time.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(datetime_timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == wall_time:
            valid_candidates.append(candidate)

    if not valid_candidates:
        raise ValidationError(
            {
                field_name: ValidationError(
                    "Datetime falls in a nonexistent local time.",
                    code="nonexistent_local_time",
                )
            }
        )
    if not any(candidate.utcoffset() == supplied_offset for candidate in valid_candidates):
        raise ValidationError(
            {
                field_name: ValidationError(
                    "Datetime offset does not match the provider timezone.",
                    code="timezone_offset_mismatch",
                )
            }
        )


def as_utc(value: datetime) -> datetime:
    return value.astimezone(datetime_timezone.utc)
