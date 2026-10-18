from odoo import http, models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_error(cls, exception):
        path = request.httprequest.path or ""
        if path.startswith("/almohit/api/") and isinstance(
            exception, http.SessionExpiredException
        ):
            from ..services.api import error
            return error(
                "AUTHENTICATION_REQUIRED",
                "Authentication required.",
                401,
            )
        return super()._handle_error(exception)
