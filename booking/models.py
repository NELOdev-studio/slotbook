from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .validation import as_utc, validate_datetime_in_timezone, validate_iana_timezone


class UserManager(BaseUserManager):
    def create_user(self, username, role, timezone_name=None, password=None, **extra_fields):
        user = self.model(
            username=username,
            role=role,
            timezone=timezone_name,
            **extra_fields,
        )
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(
            username=username,
            role=User.Role.PROVIDER,
            timezone_name="UTC",
            password=password,
            **extra_fields,
        )


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        PROVIDER = "Provider", "Provider"
        CUSTOMER = "Customer", "Customer"

    username = models.CharField(max_length=150, unique=True)
    role = models.CharField(max_length=8, choices=Role.choices, editable=False)
    timezone = models.CharField(max_length=64, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["role"]

    objects = UserManager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(role="Provider", timezone__isnull=False)
                    & ~models.Q(timezone="")
                )
                | (
                    models.Q(role="Customer")
                    & (models.Q(timezone__isnull=True) | models.Q(timezone=""))
                ),
                name="user_role_timezone_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.role not in User.Role.values:
            raise ValidationError({"role": "Role must be Provider or Customer."})
        if self.role == self.Role.PROVIDER:
            try:
                validate_iana_timezone(self.timezone)
            except ValidationError as error:
                raise ValidationError({"timezone": error.error_list})
        elif self.timezone not in (None, ""):
            raise ValidationError({"timezone": "Customers must not have a timezone."})

    def save(self, *args, **kwargs):
        if self.pk:
            old_role = type(self).objects.only("role").get(pk=self.pk).role
            if old_role != self.role:
                raise ValidationError("Application role is immutable.", code="immutable_role")
        self.full_clean()
        return super().save(*args, **kwargs)


class Service(models.Model):
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="services",
        limit_choices_to={"role": User.Role.PROVIDER},
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Service name must be nonblank."})
        if self.owner_id and self.owner.role != User.Role.PROVIDER:
            raise ValidationError({"owner": "Service owner must be a Provider."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TimeSlot(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="time_slots",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        ordering = ["starts_at", "id"]
        indexes = [
            models.Index(fields=["starts_at"], name="slot_starts_at_idx"),
            models.Index(fields=["service", "starts_at"], name="slot_service_start_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="slot_ends_after_starts",
            ),
        ]

    @property
    def provider(self):
        return self.service.owner

    def clean(self):
        super().clean()
        if self.starts_at is None or self.ends_at is None:
            raise ValidationError("Both slot endpoints are required.")
        if not self.service_id:
            return
        provider_timezone = self.service.owner.timezone
        validate_datetime_in_timezone(self.starts_at, provider_timezone, "starts_at")
        validate_datetime_in_timezone(self.ends_at, provider_timezone, "ends_at")
        starts_utc = as_utc(self.starts_at)
        ends_utc = as_utc(self.ends_at)
        if ends_utc <= starts_utc:
            raise ValidationError(
                {"ends_at": "Slot must be a half-open interval with a positive duration."}
            )
        if starts_utc <= timezone.now():
            raise ValidationError(
                {"starts_at": "Slot must start in the future."},
                code="slot_not_future",
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Booking(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"

    customer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="bookings",
        limit_choices_to={"role": User.Role.CUSTOMER},
    )
    slot = models.OneToOneField(
        TimeSlot,
        on_delete=models.PROTECT,
        related_name="booking",
    )
    status = models.CharField(
        max_length=9,
        choices=Status.choices,
        default=Status.CONFIRMED,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slot__starts_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="confirmed"),
                name="booking_confirmed_only",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "created_at"], name="booking_customer_created_idx"),
        ]

    def clean(self):
        super().clean()
        if self.customer_id and self.customer.role != User.Role.CUSTOMER:
            raise ValidationError({"customer": "Booking customer must be a Customer."})
        if self.slot_id and self.slot.starts_at <= timezone.now():
            raise ValidationError(
                {"slot": "Only future slots can be booked."},
                code="slot_not_future",
            )
        if self.status != self.Status.CONFIRMED:
            raise ValidationError({"status": "Only confirmed bookings are supported."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
