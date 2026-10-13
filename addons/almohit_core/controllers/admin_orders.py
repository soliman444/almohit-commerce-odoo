from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.orders import (
    ORDER_NOT_FOUND,
    admin_order_summary,
    get_admin_order,
    list_admin_orders,
)
from ..services.staff import require_internal_staff


class AlmohitAdminOrderController(http.Controller):

    @http.route(
        "/almohit/api/admin/orders/summary",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def summary(self, **kwargs):
        _user, err = require_internal_staff("read", "sale.order")
        if err:
            return err
        return success(admin_order_summary(request.env))

    @http.route(
        "/almohit/api/admin/orders",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_orders(self, **kwargs):
        _user, err = require_internal_staff("read", "sale.order")
        if err:
            return err
        return success(list_admin_orders(request.env, kwargs))

    @http.route(
        "/almohit/api/admin/orders/<int:order_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_order(self, order_id, **kwargs):
        _user, err = require_internal_staff("read", "sale.order")
        if err:
            return err
        data = get_admin_order(request.env, order_id)
        if not data:
            return error(ORDER_NOT_FOUND, "Order not found.", 404)
        return success(data)
