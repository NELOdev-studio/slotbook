from __future__ import annotations

import threading
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from booking.models import Booking, Service, TimeSlot, User


def _book_on_separate_connection(customer_id, slot_id, barrier, result, index):
    connections.close_all()
    barrier.wait()
    try:
        with transaction.atomic():
            slot = TimeSlot.objects.select_for_update().get(pk=slot_id)
            if Booking.objects.filter(slot=slot).exists():
                result[index] = 409
            else:
                try:
                    Booking.objects.create(
                        customer_id=customer_id,
                        slot=slot,
                    )
                    result[index] = 201
                except IntegrityError:
                    result[index] = 409
    except Exception as exc:  # pragma: no cover - reported by the harness
        result[index] = exc
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres_contention
def test_postgres_booking_contention_harness():
    if connections["default"].vendor != "postgresql":
        pytest.skip("PostgreSQL is required; SQLite cannot prove row locking.")
    try:
        connections["default"].ensure_connection()
    except Exception as exc:
        pytest.skip(f"PostgreSQL is unavailable: {exc}")

    provider = User.objects.create_user(
        "contention-provider",
        User.Role.PROVIDER,
        timezone_name="Europe/Tallinn",
    )
    customers = [
        User.objects.create_user(f"contention-customer-{i}", User.Role.CUSTOMER)
        for i in range(2)
    ]
    service = Service.objects.create(owner=provider, name="Contention")
    for _ in range(20):
        start = timezone.now().astimezone(ZoneInfo(provider.timezone)) + timedelta(days=2)
        slot = TimeSlot.objects.create(
            service=service,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
        )
        barrier = threading.Barrier(2)
        result = [None, None]
        threads = [
            threading.Thread(
                target=_book_on_separate_connection,
                args=(customer.id, slot.id, barrier, result, index),
            )
            for index, customer in enumerate(customers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(isinstance(item, Exception) for item in result), result
        assert sorted(result) == [201, 409]
        assert Booking.objects.filter(slot=slot).count() == 1
        Booking.objects.filter(slot=slot).delete()
        slot.delete()
