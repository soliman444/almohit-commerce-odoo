from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.customer import require_customer_partner
from ..services.invoices import search_customer_invoices
from ..services.orders import search_confirmed_orders
from ..services.payments import (
    owned_customer_payment,
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
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        payments = payments_for_invoices(invoices)
        return success([
            serialize_payment(payment, invoices)
            for payment in payments
        ])

    @http.route(
        "/almohit/api/payments/<int:payment_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_payment(self, payment_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        payment = owned_customer_payment(invoices, payment_id)
        if not payment:
            return error("PAYMENT_NOT_FOUND", "Payment not found.", 404)
        return success(serialize_payment(payment, invoices))
