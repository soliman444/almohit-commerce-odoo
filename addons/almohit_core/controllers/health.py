from odoo import http

from ..services.api import success


class AlmohitHealthController(http.Controller):

    @http.route(
        "/almohit/api/health",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
    )
    def health(self, **kwargs):
        return success({
            "status": "ok",
            "backend": "odoo",
            "version": "17",
        })