import json

from odoo import http
from odoo.http import request

from ..services.addresses import (
    owned_address,
    parse_address_payload,
    search_addresses,
    serialize_address,
    validate_state_country,
)
from ..services.api import error, success
from ..services.customer import commercial_partner, require_customer_partner


class AlmohitAddressController(http.Controller):

    @http.route(
        "/almohit/api/addresses",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_addresses(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        commercial = commercial_partner(partner)
        addresses = search_addresses(request.env, commercial)
        return success([serialize_address(address) for address in addresses])

    @http.route(
        "/almohit/api/addresses",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_address(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        values, parse_err = parse_address_payload(request.env, payload)
        if parse_err:
            return error(*parse_err)
        relation_err = validate_state_country(request.env, values)
        if relation_err:
            return error(*relation_err)

        commercial = commercial_partner(partner)
        values["parent_id"] = commercial.id
        # Portal users cannot create res.partner children. sudo() is scoped
        # to one child of the authenticated commercial partner.
        address = request.env["res.partner"].sudo().create(values)
        return success(serialize_address(address), status=201)

    @http.route(
        "/almohit/api/addresses/<int:address_id>",
        type="http",
        auth="user",
        methods=["PATCH"],
        csrf=False,
    )
    def update_address(self, address_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        commercial = commercial_partner(partner)
        address = owned_address(request.env, commercial, address_id)
        if not address:
            return error("ADDRESS_NOT_FOUND", "Address not found.", 404)

        values, parse_err = parse_address_payload(
            request.env,
            payload,
            partial=True,
        )
        if parse_err:
            return error(*parse_err)
        values.pop("parent_id", None)
        relation_err = validate_state_country(request.env, values, existing=address)
        if relation_err:
            return error(*relation_err)

        address.write(values)
        return success(serialize_address(address))

    @http.route(
        "/almohit/api/addresses/<int:address_id>",
        type="http",
        auth="user",
        methods=["DELETE"],
        csrf=False,
    )
    def delete_address(self, address_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        commercial = commercial_partner(partner)
        address = owned_address(request.env, commercial, address_id)
        if not address:
            return error("ADDRESS_NOT_FOUND", "Address not found.", 404)
        # Archive instead of unlink: confirmed sale.order records keep
        # partner_invoice_id / partner_shipping_id references.
        address.active = False
        return success(None)

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
