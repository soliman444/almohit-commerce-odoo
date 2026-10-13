import json

from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.customer import require_approved_customer
from ..services.orders import checkout_cart, serialize_order


class AlmohitCheckoutController(http.Controller):

    @http.route(
        "/almohit/api/checkout",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def checkout(self, **kwargs):
        partner, err = require_approved_customer()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        order, checkout_err = checkout_cart(partner, payload)
        if checkout_err:
            code, message, status, extra = checkout_err
            return error(code, message, status, extra=extra)
        return success(serialize_order(order))

    def _json_payload(self):
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return None, error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )
        if not isinstance(payload, dict):
            return None, error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )
        return payload, None
