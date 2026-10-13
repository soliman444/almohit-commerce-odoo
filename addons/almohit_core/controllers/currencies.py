from odoo import http
from odoo.http import request

from ..services.api import success
from ..services.currencies import list_display_currencies


class AlmohitCurrenciesController(http.Controller):

    @http.route(
        "/almohit/api/currencies",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def list_currencies(self, **kwargs):
        return success(list_display_currencies(request.env))
