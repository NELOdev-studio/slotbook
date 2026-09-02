from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from .api import error_payload
from .models import Booking, Service, TimeSlot
from .permissions import CustomerPermission, ProviderPermission
from .serializers import (
    BookingSerializer,
    PublicServiceSerializer,
    ServiceSerializer,
    TimeSlotSerializer,
)
from .schema import (
    BookingCreateSerializer,
    ErrorResponseSerializer,
    HealthResponseSerializer,
    ProviderProfilePatchSerializer,
    ProviderProfileSerializer,
    ServiceCreateSerializer,
    ServicePatchSerializer,
)
from .signals import booking_created
from .validation import validate_iana_timezone


class StablePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def error_response(code: str, description: str):
    return OpenApiResponse(
        response=ErrorResponseSerializer,
        description=description,
        examples=[
            OpenApiExample(
                f"{code} error",
                value={"error": {"code": code, "message": description}},
                response_only=True,
            )
        ],
    )


ERROR_RESPONSES = {
    400: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="Malformed or field-invalid input.",
    ),
    401: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="Authentication is required or credentials are invalid.",
    ),
    403: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="The authenticated user has the wrong role.",
    ),
    404: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="The requested object does not exist in the caller's scope.",
    ),
    405: error_response("method_not_allowed", "HTTP method is not allowed."),
}
PROTECTED_ERRORS = {
    401: ERROR_RESPONSES[401],
    403: ERROR_RESPONSES[403],
    405: ERROR_RESPONSES[405],
}
OWNED_ERRORS = {
    **PROTECTED_ERRORS,
    404: ERROR_RESPONSES[404],
}


@extend_schema(
    summary="Check local readiness",
    description=(
        "Unauthenticated local readiness check. Returns only a readiness "
        "status and never exposes database diagnostics or secrets."
    ),
    responses={
        200: OpenApiResponse(
            response=HealthResponseSerializer,
            description="Database connection is ready.",
        ),
        503: OpenApiResponse(
            response=HealthResponseSerializer,
            description="Database connection is not ready.",
        ),
        405: ERROR_RESPONSES[405],
    },
    auth=[],
    tags=["System"],
)
class HealthView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request):
        del request
        try:
            connection.ensure_connection()
        except DatabaseError:
            return Response(
                {"status": "not_ready"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ready"}, status=status.HTTP_200_OK)


@extend_schema_view(
    get=extend_schema(
        summary="Read Provider profile",
        responses={200: ProviderProfileSerializer, **PROTECTED_ERRORS},
        tags=["Provider"],
    ),
    patch=extend_schema(
        summary="Update Provider timezone",
        request=ProviderProfilePatchSerializer,
        responses={
            200: ProviderProfileSerializer,
            400: error_response("invalid_timezone", "Timezone must be a valid IANA name."),
            409: error_response(
                "timezone_change_conflict",
                "Timezone cannot change while future slots exist.",
            ),
            **PROTECTED_ERRORS,
        },
        tags=["Provider"],
    ),
)
class ProviderProfileView(APIView):
    permission_classes = (ProviderPermission,)

    def get(self, request):
        return Response({"role": request.user.role, "timezone": request.user.timezone})

    def patch(self, request):
        if set(request.data) - {"timezone"}:
            return Response(
                error_payload("field_not_writable", "Only timezone can be updated."),
                status=status.HTTP_400_BAD_REQUEST,
            )
        value = request.data.get("timezone")
        try:
            validate_iana_timezone(value)
        except ValidationError:
            return Response(
                error_payload("invalid_timezone", "Timezone must be a valid IANA name."),
                status=status.HTTP_400_BAD_REQUEST,
            )
        if value != request.user.timezone and request.user.services.filter(
            time_slots__starts_at__gt=timezone.now()
        ).exists():
            return Response(
                error_payload(
                    "timezone_change_conflict",
                    "Timezone cannot change while future slots exist.",
                ),
                status=status.HTTP_409_CONFLICT,
            )
        request.user.timezone = value
        request.user.save(update_fields=["timezone"])
        return Response({"role": request.user.role, "timezone": request.user.timezone})


@extend_schema_view(
    get=extend_schema(
        summary="List Provider services",
        responses={200: ServiceSerializer(many=True), **PROTECTED_ERRORS},
        tags=["Provider services"],
    ),
    post=extend_schema(
        summary="Create Provider service",
        request=ServiceCreateSerializer,
        responses={
            201: ServiceSerializer,
            400: error_response("service_name_required", "Service name must be nonblank."),
            **PROTECTED_ERRORS,
        },
        tags=["Provider services"],
    ),
)
class ProviderServiceListCreateView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    pagination_class = StablePagination
    serializer_class = ServiceSerializer

    def get(self, request):
        page = self.paginate_queryset(
            Service.objects.filter(owner=request.user).order_by("id")
        )
        return self.get_paginated_response(ServiceSerializer(page, many=True).data)

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(owner=request.user)
        return Response(
            self.get_serializer(instance).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(
        summary="Read owned Provider service",
        responses={200: ServiceSerializer, **OWNED_ERRORS},
        tags=["Provider services"],
    ),
    patch=extend_schema(
        summary="Update owned Provider service",
        request=ServicePatchSerializer,
        responses={
            200: ServiceSerializer,
            400: error_response("service_name_required", "Service name must be nonblank."),
            **OWNED_ERRORS,
        },
        tags=["Provider services"],
    ),
    delete=extend_schema(
        summary="Delete owned Provider service",
        responses={
            204: OpenApiResponse(description="Service deleted."),
            409: error_response(
                "service_has_slots",
                "Service cannot be deleted while slots exist.",
            ),
            **OWNED_ERRORS,
        },
        tags=["Provider services"],
    ),
)
class ProviderServiceDetailView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    serializer_class = ServiceSerializer

    def get_object(self, request, service_id):
        return get_object_or_404(Service, pk=service_id, owner=request.user)

    def get(self, request, service_id):
        return Response(self.get_serializer(self.get_object(request, service_id)).data)

    def patch(self, request, service_id):
        instance = self.get_object(request, service_id)
        if set(request.data) - {"name", "description"}:
            return Response(
                error_payload(
                    "field_not_writable",
                    "Only name and description can be updated.",
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(serializer.save()).data)

    def delete(self, request, service_id):
        instance = self.get_object(request, service_id)
        if instance.time_slots.exists():
            return Response(
                error_payload(
                    "service_has_slots",
                    "Service cannot be deleted while slots exist.",
                ),
                status=status.HTTP_409_CONFLICT,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        summary="List Provider time slots",
        responses={200: TimeSlotSerializer(many=True), **PROTECTED_ERRORS},
        tags=["Provider slots"],
    ),
    post=extend_schema(
        summary="Create Provider time slot",
        request=TimeSlotSerializer,
        responses={
            201: TimeSlotSerializer,
            400: error_response(
                "validation_error",
                "Slot data is malformed or violates timezone and interval rules.",
            ),
            404: error_response("service_not_found", "Owned service was not found."),
            **PROTECTED_ERRORS,
        },
        tags=["Provider slots"],
    ),
)
class ProviderSlotListCreateView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    pagination_class = StablePagination
    serializer_class = TimeSlotSerializer

    def get_queryset(self):
        return (
            TimeSlot.objects.filter(service__owner=self.request.user)
            .select_related("service__owner")
            .prefetch_related("booking")
        )

    def get(self, request):
        page = self.paginate_queryset(self.get_queryset().order_by("starts_at", "id"))
        return self.get_paginated_response(
            self.get_serializer(page, many=True).data
        )

    def post(self, request):
        if "service_id" in request.data:
            raw_service_id = request.data["service_id"]
            try:
                service_id = int(raw_service_id)
            except (TypeError, ValueError):
                return Response(
                    error_payload("validation_error", "service_id must be an integer."),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not Service.objects.filter(pk=service_id, owner=request.user).exists():
                return Response(
                    error_payload("service_not_found", "Owned service was not found."),
                    status=status.HTTP_404_NOT_FOUND,
                )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(
            self.get_serializer(instance).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(
        summary="Read owned Provider time slot",
        responses={200: TimeSlotSerializer, **OWNED_ERRORS},
        tags=["Provider slots"],
    ),
    patch=extend_schema(
        summary="Update owned Provider time slot",
        request=TimeSlotSerializer,
        responses={
            200: TimeSlotSerializer,
            400: ERROR_RESPONSES[400],
            404: error_response("service_not_found", "Owned service was not found."),
            409: error_response(
                "slot_already_booked",
                "Slot is already booked or no longer eligible for updates.",
            ),
            **PROTECTED_ERRORS,
        },
        tags=["Provider slots"],
    ),
    delete=extend_schema(
        summary="Delete owned Provider time slot",
        responses={
            204: OpenApiResponse(description="Time slot deleted."),
            409: error_response(
                "slot_already_booked",
                "Slot is already booked or no longer eligible for deletion.",
            ),
            **OWNED_ERRORS,
        },
        tags=["Provider slots"],
    ),
)
class ProviderSlotDetailView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    serializer_class = TimeSlotSerializer

    def get_object(self, request, slot_id):
        return get_object_or_404(
            TimeSlot.objects.filter(service__owner=request.user)
            .select_related("service__owner")
            .prefetch_related("booking"),
            pk=slot_id,
        )

    def get(self, request, slot_id):
        return Response(self.get_serializer(self.get_object(request, slot_id)).data)

    def _conflict(self, instance):
        if Booking.objects.filter(slot=instance).exists():
            return error_payload(
                "slot_already_booked", "Slot is already booked."
            )
        if instance.starts_at <= timezone.now():
            return error_payload(
                "slot_not_future", "Slot is no longer in the future."
            )
        return None

    def patch(self, request, slot_id):
        instance = self.get_object(request, slot_id)
        conflict = self._conflict(instance)
        if conflict:
            return Response(conflict, status=status.HTTP_409_CONFLICT)
        if "service_id" in request.data:
            try:
                service_id = int(request.data["service_id"])
            except (TypeError, ValueError):
                return Response(
                    error_payload("validation_error", "service_id must be an integer."),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not Service.objects.filter(
                pk=service_id, owner=request.user
            ).exists():
                return Response(
                    error_payload("service_not_found", "Owned service was not found."),
                    status=status.HTTP_404_NOT_FOUND,
                )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(serializer.save()).data)

    def delete(self, request, slot_id):
        instance = self.get_object(request, slot_id)
        conflict = self._conflict(instance)
        if conflict:
            return Response(conflict, status=status.HTTP_409_CONFLICT)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        summary="Discover services",
        responses={200: PublicServiceSerializer(many=True), **PROTECTED_ERRORS},
        tags=["Customer discovery"],
    ),
)
class CustomerServiceListView(GenericAPIView):
    permission_classes = (CustomerPermission,)
    pagination_class = StablePagination
    serializer_class = PublicServiceSerializer

    def get(self, request):
        queryset = Service.objects.select_related("owner").order_by("id")
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema_view(
    get=extend_schema(
        summary="Read a public service",
        responses={200: PublicServiceSerializer, **OWNED_ERRORS},
        tags=["Customer discovery"],
    ),
)
class CustomerServiceDetailView(GenericAPIView):
    permission_classes = (CustomerPermission,)
    serializer_class = PublicServiceSerializer

    def get(self, request, service_id):
        instance = get_object_or_404(
            Service.objects.select_related("owner"), pk=service_id
        )
        return Response(self.get_serializer(instance).data)


@extend_schema_view(
    get=extend_schema(
        summary="List available future slots",
        responses={200: TimeSlotSerializer(many=True), **OWNED_ERRORS},
        tags=["Customer discovery"],
    ),
)
class CustomerAvailableSlotsView(GenericAPIView):
    permission_classes = (CustomerPermission,)
    pagination_class = StablePagination
    serializer_class = TimeSlotSerializer

    def get(self, request, service_id):
        service = get_object_or_404(
            Service.objects.select_related("owner"), pk=service_id
        )
        queryset = (
            TimeSlot.objects.filter(service=service, starts_at__gt=timezone.now())
            .annotate(has_booking=Exists(Booking.objects.filter(slot=OuterRef("pk"))))
            .filter(has_booking=False)
            .select_related("service__owner")
            .order_by("starts_at", "id")
        )
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema_view(
    post=extend_schema(
        summary="Book an available slot",
        request=BookingCreateSerializer,
        responses={
            201: BookingSerializer,
            400: error_response("slot_id_required", "Only slot_id is accepted."),
            404: error_response("slot_not_found", "Slot was not found."),
            409: error_response("slot_already_booked", "Slot is already booked."),
            **PROTECTED_ERRORS,
        },
        tags=["Customer bookings"],
    ),
)
class CustomerBookingCreateView(GenericAPIView):
    permission_classes = (CustomerPermission,)
    serializer_class = BookingSerializer

    def post(self, request):
        if set(request.data) != {"slot_id"}:
            return Response(
                error_payload("slot_id_required", "Only slot_id is accepted."),
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            slot_id = int(request.data["slot_id"])
        except (TypeError, ValueError):
            return Response(
                error_payload("slot_id_required", "slot_id must be an integer."),
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                try:
                    slot = (
                        TimeSlot.objects.select_for_update()
                        .select_related("service__owner")
                        .get(pk=slot_id)
                    )
                except TimeSlot.DoesNotExist as exc:
                    raise LookupError from exc
                if slot.starts_at <= timezone.now():
                    raise ValueError("slot_not_future")
                if Booking.objects.filter(slot=slot).exists():
                    raise ValueError("slot_already_booked")
                booking = Booking.objects.create(customer=request.user, slot=slot)
        except LookupError:
            return Response(
                error_payload("slot_not_found", "Slot was not found."),
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            code = str(exc)
            message = (
                "Slot is no longer in the future."
                if code == "slot_not_future"
                else "Slot is already booked."
            )
            return Response(error_payload(code, message), status=status.HTTP_409_CONFLICT)
        except IntegrityError:
            return Response(
                error_payload("slot_already_booked", "Slot is already booked."),
                status=status.HTTP_409_CONFLICT,
            )
        booking_created.send_robust(
            sender=Booking, booking=booking, booking_id=booking.pk
        )
        return Response(
            self.get_serializer(booking).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(
        summary="List Provider bookings",
        responses={200: BookingSerializer(many=True), **PROTECTED_ERRORS},
        tags=["Provider bookings"],
    ),
)
class ProviderBookingListView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    pagination_class = StablePagination
    serializer_class = BookingSerializer

    def get(self, request):
        queryset = (
            Booking.objects.filter(slot__service__owner=request.user)
            .select_related("slot__service", "slot__service__owner")
            .order_by("slot__starts_at", "id")
        )
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema_view(
    get=extend_schema(
        summary="Read an owned Provider booking",
        responses={200: BookingSerializer, **OWNED_ERRORS},
        tags=["Provider bookings"],
    ),
)
class ProviderBookingDetailView(GenericAPIView):
    permission_classes = (ProviderPermission,)
    serializer_class = BookingSerializer

    def get(self, request, booking_id):
        instance = get_object_or_404(
            Booking.objects.filter(slot__service__owner=request.user).select_related(
                "slot__service", "slot__service__owner"
            ),
            pk=booking_id,
        )
        return Response(self.get_serializer(instance).data)
