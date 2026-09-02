from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from booking.models import Booking, Service, TimeSlot, User
from booking.signals import booking_created


def make_fixture():
    provider = User.objects.create_user(
        "api-provider",
        User.Role.PROVIDER,
        timezone_name="Europe/Tallinn",
        password="provider-secret",
    )
    other_provider = User.objects.create_user(
        "api-provider-other",
        User.Role.PROVIDER,
        timezone_name="UTC",
        password="provider-secret",
    )
    customer = User.objects.create_user(
        "api-customer", User.Role.CUSTOMER, password="customer-secret"
    )
    other_customer = User.objects.create_user(
        "api-customer-other", User.Role.CUSTOMER, password="customer-secret"
    )
    service = Service.objects.create(owner=provider, name="Consultation")
    other_service = Service.objects.create(owner=other_provider, name="Other")
    start = timezone.now().astimezone(ZoneInfo("Europe/Tallinn")) + timedelta(days=2)
    slot = TimeSlot.objects.create(
        service=service,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
    )
    return provider, other_provider, customer, other_customer, service, other_service, slot


def auth_client(username, secret):
    client = APIClient()
    response = client.post(
        "/api/auth/token/", {"username": username, "password": secret}, format="json"
    )
    assert response.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return client


@pytest.mark.django_db
def test_token_and_refresh_and_auth_failures():
    provider, *_ = make_fixture()
    client = APIClient()
    token = client.post(
        "/api/auth/token/",
        {"username": provider.username, "password": "provider-secret"},
        format="json",
    )
    assert token.status_code == 200
    refreshed = client.post(
        "/api/auth/token/refresh/", {"refresh": token.data["refresh"]}, format="json"
    )
    assert refreshed.status_code == 200
    assert client.get("/api/provider/profile/").status_code == 401
    invalid = client.post(
        "/api/auth/token/",
        {"username": provider.username, "password": "wrong"},
        format="json",
    )
    assert invalid.status_code == 401


@pytest.mark.django_db
def test_provider_profile_timezone_rules_and_role_permissions():
    provider, _, customer, _, service, _, slot = make_fixture()
    provider_client = auth_client(provider.username, "provider-secret")
    assert provider_client.get("/api/provider/profile/").json() == {
        "role": "Provider",
        "timezone": "Europe/Tallinn",
    }
    conflict = provider_client.patch(
        "/api/provider/profile/", {"timezone": "UTC"}, format="json"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "timezone_change_conflict"
    invalid = provider_client.patch(
        "/api/provider/profile/", {"timezone": "Mars/Olympus"}, format="json"
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_timezone"
    wrong_role = auth_client(customer.username, "customer-secret")
    assert wrong_role.get("/api/provider/profile/").status_code == 403
    slot.delete()
    changed = provider_client.patch(
        "/api/provider/profile/", {"timezone": "UTC"}, format="json"
    )
    assert changed.status_code == 200


@pytest.mark.django_db
def test_provider_service_crud_and_ownership():
    provider, other_provider, customer, _, service, other_service, _ = make_fixture()
    client = auth_client(provider.username, "provider-secret")
    listed = client.get("/api/provider/services/")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.data["results"]] == [service.id]
    created = client.post(
        "/api/provider/services/",
        {"name": "  New service  ", "description": "details"},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["name"] == "New service"
    blank = client.post("/api/provider/services/", {"name": " "}, format="json")
    assert blank.status_code == 400
    assert client.get(f"/api/provider/services/{other_service.id}/").status_code == 404
    assert client.patch(
        f"/api/provider/services/{service.id}/",
        {"owner": other_provider.id},
        format="json",
    ).status_code == 400
    assert client.delete(f"/api/provider/services/{service.id}/").status_code == 409
    empty = Service.objects.create(owner=provider, name="Empty")
    assert client.delete(f"/api/provider/services/{empty.id}/").status_code == 204
    assert auth_client(customer.username, "customer-secret").get(
        "/api/provider/services/"
    ).status_code == 403


@pytest.mark.django_db
def test_provider_slot_crud_timezone_and_conflicts():
    provider, _, customer, _, service, other_service, slot = make_fixture()
    client = auth_client(provider.username, "provider-secret")
    start = (timezone.now() + timedelta(days=3)).astimezone(
        ZoneInfo("Europe/Tallinn")
    )
    payload = {
        "service_id": service.id,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=45)).isoformat(),
    }
    created = client.post("/api/provider/slots/", payload, format="json")
    assert created.status_code == 201
    assert created.data["starts_at"].endswith("Z")
    assert created.data["availability"] == "available"
    assert client.post(
        "/api/provider/slots/",
        {**payload, "service_id": other_service.id},
        format="json",
    ).status_code == 404
    no_offset = client.post(
        "/api/provider/slots/",
        {**payload, "starts_at": "2099-01-01T10:00:00"},
        format="json",
    )
    assert no_offset.status_code == 400
    assert no_offset.json()["error"]["code"] == "timezone_offset_required"
    mismatch = client.post(
        "/api/provider/slots/",
        {
            **payload,
            "starts_at": "2099-01-15T10:00:00+00:00",
            "ends_at": "2099-01-15T10:30:00+00:00",
        },
        format="json",
    )
    assert mismatch.json()["error"]["code"] == "timezone_offset_mismatch"
    assert client.patch(
        f"/api/provider/slots/{slot.id}/",
        {"ends_at": (start + timedelta(minutes=40)).isoformat()},
        format="json",
    ).status_code == 200
    Booking.objects.create(customer=customer, slot=slot)
    booked_patch = client.patch(
        f"/api/provider/slots/{slot.id}/", {"service_id": service.id}, format="json"
    )
    assert booked_patch.status_code == 409
    assert booked_patch.json()["error"]["code"] == "slot_already_booked"
    assert client.delete(f"/api/provider/slots/{slot.id}/").json()["error"]["code"] == "slot_already_booked"


@pytest.mark.django_db
def test_api_dst_wall_time_validation_and_utc_serialization():
    provider, _, _, _, service, _, _ = make_fixture()
    client = auth_client(provider.username, "provider-secret")
    gap = client.post(
        "/api/provider/slots/",
        {
            "service_id": service.id,
            "starts_at": "2027-03-28T03:30:00+02:00",
            "ends_at": "2027-03-28T04:00:00+03:00",
        },
        format="json",
    )
    assert gap.status_code == 400
    assert gap.json()["error"]["code"] == "nonexistent_local_time"
    repeated = []
    for offset, end_time in (("+03:00", "03:50"), ("+02:00", "04:00")):
        response = client.post(
            "/api/provider/slots/",
            {
                "service_id": service.id,
                "starts_at": f"2027-10-31T03:30:00{offset}",
                "ends_at": f"2027-10-31T{end_time}:00{offset}",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["starts_at"].endswith("Z")
        repeated.append(response.data["starts_at"])
    assert repeated[0] != repeated[1]
    transition = client.post(
        "/api/provider/slots/",
        {
            "service_id": service.id,
            "starts_at": "2027-03-28T01:30:00+02:00",
            "ends_at": "2027-03-28T04:30:00+03:00",
        },
        format="json",
    )
    assert transition.status_code == 201


@pytest.mark.django_db
def test_customer_discovery_booking_and_provider_reads():
    provider, _, customer, other_customer, service, _, slot = make_fixture()
    customer_client = auth_client(customer.username, "customer-secret")
    assert customer_client.get("/api/services/").status_code == 200
    service_data = customer_client.get(f"/api/services/{service.id}/").json()
    assert service_data["timezone"] == "Europe/Tallinn"
    available = customer_client.get(
        f"/api/services/{service.id}/available-slots/"
    )
    assert available.status_code == 200
    assert available.data["results"][0]["availability"] == "available"
    events = []
    booking_created.connect(lambda **kwargs: events.append(kwargs), weak=False)
    booked = customer_client.post(
        "/api/bookings/", {"slot_id": slot.id}, format="json"
    )
    assert booked.status_code == 201
    assert booked.data["status"] == "confirmed"
    assert booked.data["starts_at"].endswith("Z")
    assert len(events) == 1
    assert customer_client.post(
        "/api/bookings/", {"slot_id": slot.id}, format="json"
    ).json()["error"]["code"] == "slot_already_booked"
    other_client = auth_client(other_customer.username, "customer-secret")
    duplicate = other_client.post(
        "/api/bookings/", {"slot_id": slot.id}, format="json"
    )
    assert duplicate.status_code == 409
    missing = customer_client.post(
        "/api/bookings/", {"slot_id": 999999}, format="json"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "slot_not_found"
    provider_client = auth_client(provider.username, "provider-secret")
    bookings = provider_client.get("/api/provider/bookings/")
    assert bookings.status_code == 200
    assert bookings.data["results"][0]["id"] == booked.data["id"]
    assert provider_client.get(
        f"/api/provider/bookings/{booked.data['id']}/"
    ).status_code == 200
    assert customer_client.get("/api/provider/bookings/").status_code == 403
