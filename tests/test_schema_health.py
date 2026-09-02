from unittest.mock import patch

import pytest
from django.db import OperationalError
from rest_framework.test import APIClient


FROZEN_SCHEMA_PATHS = {
    "/api/auth/token/": {"post"},
    "/api/auth/token/refresh/": {"post"},
    "/api/provider/profile/": {"get", "patch"},
    "/api/provider/services/": {"get", "post"},
    "/api/provider/services/{service_id}/": {"get", "patch", "delete"},
    "/api/provider/slots/": {"get", "post"},
    "/api/provider/slots/{slot_id}/": {"get", "patch", "delete"},
    "/api/services/": {"get"},
    "/api/services/{service_id}/": {"get"},
    "/api/services/{service_id}/available-slots/": {"get"},
    "/api/bookings/": {"post"},
    "/api/provider/bookings/": {"get"},
    "/api/provider/bookings/{booking_id}/": {"get"},
    "/api/health/": {"get"},
}


@pytest.mark.django_db
def test_schema_has_exact_frozen_paths_and_methods():
    response = APIClient().get(
        "/api/schema/",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    schema = response.json()
    actual_paths = {
        path: {
            method
            for method in operations
            if method in {"get", "post", "patch", "delete", "put", "options", "head"}
        }
        for path, operations in schema["paths"].items()
    }
    assert actual_paths == FROZEN_SCHEMA_PATHS


@pytest.mark.django_db
def test_schema_documents_jwt_pagination_errors_and_utc_datetime_contract():
    schema = APIClient().get(
        "/api/schema/",
        HTTP_ACCEPT="application/json",
    ).json()

    assert schema["components"]["securitySchemes"]["jwtAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert "security" not in schema["paths"]["/api/auth/token/"]["post"]
    assert "security" not in schema["paths"]["/api/auth/token/refresh/"]["post"]
    assert "security" not in schema["paths"]["/api/health/"]["get"]
    assert schema["paths"]["/api/services/"]["get"]["security"] == [{"jwtAuth": []}]
    assert schema["paths"]["/api/services/"]["get"]["parameters"] == [
        {
            "name": "page",
            "required": False,
            "in": "query",
            "description": "A page number within the paginated result set.",
            "schema": {"type": "integer"},
        },
        {
            "name": "page_size",
            "required": False,
            "in": "query",
            "description": "Number of results to return per page.",
            "schema": {"type": "integer"},
        },
    ]
    service_post = schema["paths"]["/api/provider/services/"]["post"]
    assert (
        service_post["responses"]["400"]["content"]["application/json"]["examples"][
            "ServiceNameRequiredError"
        ]["value"]["error"]["code"]
        == "service_name_required"
    )
    slot = schema["components"]["schemas"]["TimeSlot"]
    assert slot["properties"]["starts_at"]["format"] == "date-time"
    assert slot["properties"]["ends_at"]["format"] == "date-time"
    assert "UTC Z" in slot["properties"]["starts_at"]["description"]
    assert schema["components"]["schemas"]["AvailabilityEnum"]["enum"] == [
        "available",
        "booked",
    ]
    assert schema["components"]["schemas"]["Booking"]["properties"]["status"][
        "allOf"
    ][0]["$ref"].endswith("/BookingStatusEnum")
    error_codes = schema["components"]["schemas"]["CodeEnum"]
    assert "service_name_required" in error_codes["enum"]
    assert "timezone_offset_required" in error_codes["enum"]
    assert "timezone_offset_mismatch" in error_codes["enum"]
    assert "nonexistent_local_time" in error_codes["enum"]
    assert (
        schema["paths"]["/api/provider/slots/"]["post"]["requestBody"]["content"][
            "application/json"
        ]["examples"]["DSTRepeatedWallTimeInput"]["value"]["starts_at"]
        == "2027-10-31T03:30:00+03:00"
    )
    assert (
        schema["paths"]["/api/provider/slots/"]["post"]["responses"]["201"][
            "content"
        ]["application/json"]["examples"]["UTCSerializedSlot"]["value"]["starts_at"]
        == "2027-01-15T08:00:00Z"
    )
    assert "405" in schema["paths"]["/api/provider/slots/{slot_id}/"]["delete"][
        "responses"
    ]


def test_swagger_ui_is_public_and_points_to_schema():
    response = APIClient().get("/api/docs/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert b"SwaggerUIBundle" in response.content
    assert b"/api/schema/" in response.content


def test_health_is_public_and_returns_only_readiness_status():
    client = APIClient()

    with patch("booking.views.connection.ensure_connection"):
        ready = client.get("/api/health/")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    with patch(
        "booking.views.connection.ensure_connection",
        side_effect=OperationalError("database unavailable"),
    ):
        unavailable = client.get("/api/health/")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"status": "not_ready"}
    assert "database" not in unavailable.content.decode().lower()
