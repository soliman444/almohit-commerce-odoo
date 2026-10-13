from odoo import http
from odoo.http import request

from ..services.api import error, read_json_body, success
from ..services.customer import require_customer_partner
from ..services.invoices import owned_customer_invoice, search_customer_invoices, serialize_invoice
from ..services.online_payments import serialize_transaction, start_invoice_payment
from ..services.orders import search_confirmed_orders


class AlmohitInvoiceController(http.Controller):

    @http.route(
        "/almohit/api/invoices",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_invoices(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        return success([serialize_invoice(move) for move in invoices])

    @http.route(
        "/almohit/api/invoices/<int:invoice_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_invoice(self, invoice_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoice = owned_customer_invoice(orders, invoice_id)
        if not invoice:
            return error("INVOICE_NOT_FOUND", "Invoice not found.", 404)
        return success(serialize_invoice(invoice))

    @http.route(
        "/almohit/api/invoices/<int:invoice_id>/payment",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def pay_invoice(self, invoice_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoice = owned_customer_invoice(orders, invoice_id)
        if not invoice:
            return error("INVOICE_NOT_FOUND", "Invoice not found.", 404)
        tx, pay_err = start_invoice_payment(
            partner,
            invoice,
            payload.get("provider_id"),
        )
        if pay_err:
            return pay_err
        invoices = search_customer_invoices(orders)
        return success(serialize_transaction(tx, invoices, include_flow=True))
