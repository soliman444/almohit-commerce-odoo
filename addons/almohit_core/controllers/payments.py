from odoo import http
from odoo.http import request

from ..services.api import error, paginated_success, parse_pagination, success
from ..services.customer import require_almohit_group, require_customer_partner
from ..services.invoices import search_customer_invoices
from ..services.orders import search_confirmed_orders
from ..services.payments import (
    owned_customer_payment,
    parse_payment_list_params,
    payments_for_invoices,
    serialize_payment,
)


class AlmohitPaymentController(http.Controller):

    @http.route(
        "/almohit/api/payments",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_payments(self, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_buyer",
            "almohit_core.group_almohit_merchant",
        )
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        page, page_size, err = parse_pagination(kwargs)
        if err:
            return err
        extra, order, err = parse_payment_list_params(kwargs)
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        payments = payments_for_invoices(invoices)
        payments = payments.search(
            [("id", "in", payments.ids)] + extra,
            order=order,
        )
        return paginated_success(
            payments,
            lambda payment: serialize_payment(payment, invoices),
            page,
            page_size,
        )

    @http.route(
        "/almohit/api/payments/<int:payment_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_payment(self, payment_id, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_buyer",
            "almohit_core.group_almohit_merchant",
        )
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        payment = owned_customer_payment(invoices, payment_id)
        if not payment:
            return error("PAYMENT_NOT_FOUND", "Payment not found.", 404)
        return success(serialize_payment(payment, invoices))
