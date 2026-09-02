from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from booking.views import (
    CustomerAvailableSlotsView,
    CustomerBookingCreateView,
    CustomerServiceDetailView,
    CustomerServiceListView,
    ProviderBookingDetailView,
    ProviderBookingListView,
    ProviderProfileView,
    ProviderServiceDetailView,
    ProviderServiceListCreateView,
    ProviderSlotDetailView,
    ProviderSlotListCreateView,
)
from booking.views import HealthView
from booking.schema import (
    ErrorResponseSerializer,
    TokenAccessResponseSerializer,
    TokenObtainRequestSerializer,
    TokenPairResponseSerializer,
    TokenRefreshRequestSerializer,
)


class TokenObtainSchemaView(TokenObtainPairView):
    @extend_schema(
        summary="Obtain JWT access and refresh tokens",
        request=TokenObtainRequestSerializer,
        responses={
            200: TokenPairResponseSerializer,
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Invalid credentials.",
            ),
            405: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="HTTP method is not allowed.",
            ),
        },
        auth=[],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class TokenRefreshSchemaView(TokenRefreshView):
    @extend_schema(
        summary="Refresh a JWT access token",
        request=TokenRefreshRequestSerializer,
        responses={
            200: TokenAccessResponseSerializer,
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Invalid or expired refresh token.",
            ),
            405: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="HTTP method is not allowed.",
            ),
        },
        auth=[],
        tags=["Authentication"],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

urlpatterns = [
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/health/", HealthView.as_view(), name="health"),
    path("api/auth/token/", TokenObtainSchemaView.as_view(), name="token"),
    path(
        "api/auth/token/refresh/",
        TokenRefreshSchemaView.as_view(),
        name="token-refresh",
    ),
    path("api/provider/profile/", ProviderProfileView.as_view(), name="provider-profile"),
    path("api/provider/services/", ProviderServiceListCreateView.as_view(), name="provider-services"),
    path(
        "api/provider/services/<int:service_id>/",
        ProviderServiceDetailView.as_view(),
        name="provider-service-detail",
    ),
    path("api/provider/slots/", ProviderSlotListCreateView.as_view(), name="provider-slots"),
    path(
        "api/provider/slots/<int:slot_id>/",
        ProviderSlotDetailView.as_view(),
        name="provider-slot-detail",
    ),
    path("api/services/", CustomerServiceListView.as_view(), name="services"),
    path(
        "api/services/<int:service_id>/",
        CustomerServiceDetailView.as_view(),
        name="service-detail",
    ),
    path(
        "api/services/<int:service_id>/available-slots/",
        CustomerAvailableSlotsView.as_view(),
        name="available-slots",
    ),
    path("api/bookings/", CustomerBookingCreateView.as_view(), name="bookings"),
    path("api/provider/bookings/", ProviderBookingListView.as_view(), name="provider-bookings"),
    path(
        "api/provider/bookings/<int:booking_id>/",
        ProviderBookingDetailView.as_view(),
        name="provider-booking-detail",
    ),
]
