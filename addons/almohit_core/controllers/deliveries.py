from odoo import http
from odoo.http import request

from ..services.api import error, paginated_success, parse_pagination, read_json_body, success
from ..services.customer import require_almohit_group, require_distributor_partner
from ..services.stock import (
    owned_assigned_delivery,
    parse_delivery_list_params,
    search_assigned_deliveries,
    serialize_picking,
    update_assigned_delivery_status,
)


class AlmohitDeliveryController(http.Controller):

    @http.route(
        "/almohit/api/deliveries",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_deliveries(self, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_distributor",
        )
        if err:
            return err
        partner, err = require_distributor_partner()
        if err:
            return err
        page, page_size, err = parse_pagination(kwargs)
        if err:
            return err
        extra, order, err = parse_delivery_list_params(kwargs)
        if err:
            return err
        pickings = search_assigned_deliveries(request.env, request.env.user)
        pickings = pickings.search(
            [("id", "in", pickings.ids)] + extra,
            order=order,
        )
        return paginated_success(pickings, serialize_picking, page, page_size)

    @http.route(
        "/almohit/api/deliveries/<int:picking_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_delivery(self, picking_id, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_distributor",
        )
        if err:
            return err
        partner, err = require_distributor_partner()
        if err:
            return err
        picking = owned_assigned_delivery(request.env, request.env.user, picking_id)
        if not picking:
            return error("DELIVERY_NOT_FOUND", "Delivery not found.", 404)
        return success(serialize_picking(picking))

    @http.route(
        "/almohit/api/deliveries/<int:picking_id>/status",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def update_delivery_status(self, picking_id, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_distributor",
        )
        if err:
            return err
        partner, err = require_distributor_partner()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err

        extra_keys = set(payload) - {"status"}
        if extra_keys:
            return error(
                "INVALID_FIELDS",
                "One or more delivery status fields are not allowed.",
                400,
            )

        picking = owned_assigned_delivery(request.env, request.env.user, picking_id)
        if not picking:
            return error("DELIVERY_NOT_FOUND", "Delivery not found.", 404)
        updated, update_err = update_assigned_delivery_status(picking, payload.get("status"))
        if update_err:
            code, message, status, extra = update_err
            return error(code, message, status, extra=extra)
        return success(serialize_picking(updated))
