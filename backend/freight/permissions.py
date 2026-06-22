from __future__ import annotations

from rest_framework import permissions

from .authentication import has_permission


SAFE_ACTIONS = {"list", "retrieve", "metadata", "options"}


class HasFreightPermission(permissions.BasePermission):
    """Module-level authorization for CourieDelivery.

    Viewsets declare a permission_namespace such as "pricing" or "master".
    Safe requests require "<namespace>.view"; writes require "<namespace>.manage".
    Custom actions can override that through permission_action_map.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        profile = getattr(user, "freight_profile", None)
        if not profile or not profile.is_active:
            return False

        required = getattr(view, "permission_required", None)
        if required is None:
            action = getattr(view, "action", "")
            action_map = getattr(view, "permission_action_map", {}) or {}
            required = action_map.get(action)
        if required is None:
            namespace = getattr(view, "permission_namespace", "")
            if namespace:
                action = getattr(view, "action", "")
                suffix = "view" if request.method in permissions.SAFE_METHODS or action in SAFE_ACTIONS else "manage"
                required = f"{namespace}.{suffix}"
        if required is None:
            return True

        if isinstance(required, str):
            required = [required]
        return any(has_permission(profile, permission) for permission in required)
