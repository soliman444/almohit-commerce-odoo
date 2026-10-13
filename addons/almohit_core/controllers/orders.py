from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.customer import require_customer_partner
from ..services.orders import owned_confirmed_order, search_confirmed_orders, serialize_order


class AlmohitOrderController(http.Controller):

    @http.route(
        "/almohit/api/orders",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_orders(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        return success([serialize_order(order) for order in orders])

    @http.route(
        "/almohit/api/orders/<int:order_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_order(self, order_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        order = owned_confirmed_order(request.env, partner, order_id)
        if not order:
            return error("ORDER_NOT_FOUND", "Order not found.", 404)
        return success(serialize_order(order))
