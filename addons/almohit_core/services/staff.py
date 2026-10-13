from odoo.exceptions import AccessError
from odoo.http import request

from .api import error


def require_internal_staff(access="read", model="product.template"):
    """Internal Odoo users only. Portal/customer sessions are rejected."""
    user = request.env.user
    if not user or user._is_public():
        return None, error(
            "UNAUTHENTICATED",
            "Staff authentication is required.",
            401,
        )
    if user.share:
        return None, error(
            "STAFF_REQUIRED",
            "Customer sessions cannot manage products.",
            403,
        )
    if access not in ("read", "create", "write", "unlink"):
        access = "read"
    try:
        request.env[model].check_access_rights(access)
    except AccessError:
        return None, error(
            "PERMISSION_DENIED",
            "You cannot manage products.",
            403,
        )
    return user, None
