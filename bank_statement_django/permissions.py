from rest_framework.permissions import BasePermission


class IsOwnerOrStaff(BasePermission):
    """Object-level access: the user who uploaded the statement, or staff with the view permission."""

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if obj.owner_id == user.pk:
            return True
        return bool(user.is_staff and user.has_perm("bank_statement_django.view_statementanalysis"))
