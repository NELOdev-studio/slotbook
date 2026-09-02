from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo

import pytest
from django.core import management
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.utils import timezone

from booking.models import Booking, Service, TimeSlot, User


def make_users():
    provider = User.objects.create_user(
        "provider-test",
        User.Role.PROVIDER,
        timezone_name="Europe/Tallinn",
    )
    customer = User.objects.create_user("customer-test", User.Role.CUSTOMER)
    return provider, customer


def make_slot(provider):
    service = Service.objects.create(owner=provider, name="  Consultation  ")
    start = timezone.now().astimezone(ZoneInfo(provider.timezone)) + timedelta(days=2)
    return service, TimeSlot.objects.create(
        service=service,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
    )


@pytest.mark.django_db
def test_relationships_and_safe_deletion():
    provider, customer = make_users()
    service, slot = make_slot(provider)
    booking = Booking.objects.create(customer=customer, slot=slot)

    assert service.owner == provider
    assert slot.service == service
    assert slot.provider == provider
    assert booking.customer == customer
    assert booking.slot == slot
    with pytest.raises(ProtectedError):
        provider.delete()
    with pytest.raises(ProtectedError):
        service.delete()


@pytest.mark.django_db
def test_roles_are_exact_and_immutable():
    provider, customer = make_users()
    assert {provider.role, customer.role} == {"Provider", "Customer"}
    provider.role = User.Role.CUSTOMER
    with pytest.raises(ValidationError, match="immutable"):
        provider.save()

    customer.timezone = "Europe/Tallinn"
    with pytest.raises(ValidationError):
        customer.full_clean()


@pytest.mark.django_db
def test_provider_timezone_and_owner_roles_are_validated():
    with pytest.raises(ValidationError) as timezone_error:
        User(username="bad-provider", role=User.Role.PROVIDER, timezone="Mars/Olympus").full_clean()
    assert timezone_error.value.error_dict["timezone"][0].code == "invalid_timezone"

    provider, customer = make_users()
    with pytest.raises(ValidationError):
        Service(owner=customer, name="Not allowed").full_clean()

    service, slot = make_slot(provider)
    with pytest.raises(ValidationError):
        Booking(customer=provider, slot=slot).full_clean()


@pytest.mark.django_db
def test_timezone_validation_primitives_cover_offset_and_dst():
    provider, _ = make_users()
    service = Service.objects.create(owner=provider, name="DST test")
    valid = datetime(2027, 1, 15, 10, tzinfo=ZoneInfo("Europe/Tallinn"))
    TimeSlot.objects.create(
        service=service,
        starts_at=valid + timedelta(days=1),
        ends_at=valid + timedelta(days=1, minutes=30),
    )
    with pytest.raises(ValidationError) as naive_error:
        TimeSlot(
            service=service,
            starts_at=datetime(2099, 1, 15, 10),
            ends_at=datetime(2099, 1, 15, 10, 30),
        ).full_clean()
    assert naive_error.value.error_dict["starts_at"][0].code == "timezone_offset_required"

    with pytest.raises(ValidationError) as gap_error:
        TimeSlot(
            service=service,
            starts_at=datetime(
                2099,
                3,
                29,
                3,
                30,
                tzinfo=datetime_timezone(timedelta(hours=2)),
            ),
            ends_at=datetime(
                2099,
                3,
                29,
                4,
                0,
                tzinfo=datetime_timezone(timedelta(hours=2)),
            ),
        ).full_clean()
    assert gap_error.value.error_dict["starts_at"][0].code == "nonexistent_local_time"

    with pytest.raises(ValidationError) as mismatch_error:
        TimeSlot(
            service=service,
            starts_at=datetime(2099, 1, 15, 10, tzinfo=datetime_timezone.utc),
            ends_at=datetime(2099, 1, 15, 10, 30, tzinfo=datetime_timezone.utc),
        ).full_clean()
    assert mismatch_error.value.error_dict["starts_at"][0].code == "timezone_offset_mismatch"


@pytest.mark.django_db
def test_interval_is_future_and_strictly_ordered():
    provider, _ = make_users()
    service = Service.objects.create(owner=provider, name="Validation test")
    start = timezone.now() + timedelta(days=1)
    with pytest.raises(ValidationError):
        TimeSlot(service=service, starts_at=start, ends_at=start).full_clean()
    with pytest.raises(ValidationError):
        TimeSlot(
            service=service,
            starts_at=timezone.now() - timedelta(minutes=1),
            ends_at=timezone.now() + timedelta(minutes=10),
        ).full_clean()


@pytest.mark.django_db
def test_booking_slot_uniqueness_and_confirmed_only_state():
    provider, customer = make_users()
    _, slot = make_slot(provider)
    Booking.objects.create(customer=customer, slot=slot)
    other_customer = User.objects.create_user("customer-other", User.Role.CUSTOMER)
    with pytest.raises(ValidationError):
        Booking(customer=other_customer, slot=slot).full_clean()

    duplicate = Booking(customer=other_customer, slot=slot)
    with pytest.raises(IntegrityError), transaction.atomic():
        Booking.objects.bulk_create([duplicate])
    assert Booking.objects.filter(slot=slot, status=Booking.Status.CONFIRMED).count() == 1


@pytest.mark.django_db
def test_seed_command_is_reproducible():
    management.call_command("seed_demo", verbosity=0)
    first = list(
        User.objects.values_list("username", "role", "timezone").order_by("username")
    )
    first_slots = list(
        TimeSlot.objects.values_list(
            "service__name", "starts_at", "ends_at"
        ).order_by("starts_at")
    )
    management.call_command("seed_demo", verbosity=0)
    assert list(
        User.objects.values_list("username", "role", "timezone").order_by("username")
    ) == first
    assert list(
        TimeSlot.objects.values_list(
            "service__name", "starts_at", "ends_at"
        ).order_by("starts_at")
    ) == first_slots
    assert User.objects.filter(username__startswith="demo-").count() == 2
    assert TimeSlot.objects.count() == 2


@pytest.mark.django_db
def test_seed_command_can_configure_local_synthetic_demo_password():
    management.call_command("seed_demo", password="local-demo-password", verbosity=0)

    assert User.objects.get(username="demo-provider").check_password("local-demo-password")
    assert User.objects.get(username="demo-customer").check_password("local-demo-password")
