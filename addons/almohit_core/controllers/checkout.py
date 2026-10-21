import json

from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.customer import require_approved_customer, require_merchant_group
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
        err = require_merchant_group()
        if err:
            return err

        partner, err = require_approved_customer()
        if err:
            return err

        payload, err = self._json_payload()
        if err:
            return err

        # Checkout only accepts an optional additional delivery address.
        extra_keys = set(payload) - {"shipping_address_id"}
        if extra_keys:
            return error(
                "INVALID_FIELDS",
                "Only shipping_address_id is allowed.",
                400,
            )

        shipping_address_id = payload.get("shipping_address_id")

        # shipping_address_id is optional.
        # If omitted, the merchant's main res.partner address will be used.
        if shipping_address_id not in (None, "", False):
            if (
                isinstance(shipping_address_id, bool)
                or not isinstance(shipping_address_id, int)
                or shipping_address_id < 1
            ):
                return error(
                    "INVALID_CHECKOUT",
                    "shipping_address_id must be a positive integer.",
                    400,
                )

        order, checkout_err = checkout_cart(partner, payload)

        if checkout_err:
            code, message, status, extra = checkout_err
            return error(
                code,
                message,
                status,
                extra=extra,
            )

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
