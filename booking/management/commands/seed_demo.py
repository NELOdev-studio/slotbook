from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from booking.models import Booking, Service, TimeSlot, User


class Command(BaseCommand):
    help = "Create deterministic synthetic Provider, Customer, services, and slots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the existing synthetic dataset before recreating it.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            Booking.objects.filter(customer__username__startswith="demo-").delete()
            TimeSlot.objects.filter(service__owner__username="demo-provider").delete()
            Service.objects.filter(owner__username="demo-provider").delete()
            User.objects.filter(username__startswith="demo-").delete()

        provider = User.objects.filter(username="demo-provider").first()
        if provider is None:
            provider = User(
                username="demo-provider",
                role=User.Role.PROVIDER,
                timezone="Europe/Tallinn",
            )
            provider.set_unusable_password()
            provider.save()
        if provider.role != User.Role.PROVIDER or provider.timezone != "Europe/Tallinn":
            raise CommandError("Existing demo-provider has incompatible immutable identity.")
        provider.set_unusable_password()
        provider.save(update_fields=["password", "last_login"])

        customer = User.objects.filter(username="demo-customer").first()
        if customer is None:
            customer = User(username="demo-customer", role=User.Role.CUSTOMER)
            customer.set_unusable_password()
            customer.save()
        if customer.role != User.Role.CUSTOMER:
            raise CommandError("Existing demo-customer has incompatible immutable identity.")
        customer.set_unusable_password()
        customer.save(update_fields=["password", "last_login"])

        service, _ = Service.objects.get_or_create(
            owner=provider,
            name="Demo consultation",
            defaults={"description": "Synthetic service for local development."},
        )
        zone = ZoneInfo(provider.timezone)
        slot_values = (
            (
                datetime(2099, 4, 15, 10, 0, tzinfo=zone),
                datetime(2099, 4, 15, 10, 30, tzinfo=zone),
            ),
            (
                datetime(2099, 4, 15, 11, 0, tzinfo=zone),
                datetime(2099, 4, 15, 11, 30, tzinfo=zone),
            ),
        )
        for starts_at, ends_at in slot_values:
            TimeSlot.objects.get_or_create(
                service=service,
                starts_at=starts_at,
                ends_at=ends_at,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Synthetic demo dataset ready: demo-provider, demo-customer, "
                "one service, and two future slots."
            )
        )
