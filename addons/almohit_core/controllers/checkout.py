import json

from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.customer import require_almohit_group, require_approved_customer
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
        err = require_almohit_group(
            "almohit_core.group_almohit_buyer",
            "almohit_core.group_almohit_merchant",
        )
        if err:
            return err
        partner, err = require_approved_customer()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        extra_keys = set(payload) - {"shipping_address_id", "invoice_address_id"}
        if extra_keys:
            return error(
                "INVALID_FIELDS",
                "Only shipping_address_id and invoice_address_id are allowed.",
                400,
            )

        shipping_address_id = payload.get("shipping_address_id")
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

        invoice_address_id = payload.get("invoice_address_id")
        if (
            isinstance(invoice_address_id, bool)
            or not isinstance(invoice_address_id, int)
            or invoice_address_id < 1
        ):
            return error(
                "INVALID_CHECKOUT",
                "invoice_address_id must be a positive integer.",
                400,
            )

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
