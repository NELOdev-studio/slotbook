from rest_framework.permissions import BasePermission

from .models import User


class RolePermission(BasePermission):
    required_role = None

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                self.required_role is None
                or request.user.role == self.required_role
            )
        )


class ProviderPermission(RolePermission):
    required_role = User.Role.PROVIDER


class CustomerPermission(RolePermission):
    required_role = User.Role.CUSTOMER
