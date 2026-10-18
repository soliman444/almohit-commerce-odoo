from odoo import http
from odoo.http import request

from ..services.api import error, paginated_success, parse_pagination, success
from ..services.customer import require_customer_partner, require_merchant_group
from ..services.orders import (
    confirmed_order_domain,
    owned_confirmed_order,
    parse_order_list_params,
    serialize_order,
)


class AlmohitOrderController(http.Controller):

    @http.route(
        "/almohit/api/orders",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_orders(self, **kwargs):
        err = require_merchant_group()
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        page, page_size, err = parse_pagination(kwargs)
        if err:
            return err
        extra, order, err = parse_order_list_params(kwargs)
        if err:
            return err
        orders = request.env["sale.order"].sudo().search(
            confirmed_order_domain(partner) + extra,
            order=order,
        )
        return paginated_success(orders, serialize_order, page, page_size)

    @http.route(
        "/almohit/api/orders/<int:order_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_order(self, order_id, **kwargs):
        err = require_merchant_group()
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        order = owned_confirmed_order(request.env, partner, order_id)
        if not order:
            return error("ORDER_NOT_FOUND", "Order not found.", 404)
        return success(serialize_order(order))
