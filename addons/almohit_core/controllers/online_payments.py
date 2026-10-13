from odoo import http
from odoo.http import request

from ..services.api import error, read_json_body, success
from ..services.customer import require_customer_partner
from ..services.invoices import owned_customer_invoice, search_customer_invoices
from ..services.online_payments import (
    compatible_providers,
    owned_customer_transaction,
    serialize_provider,
    serialize_transaction,
    transactions_for_invoices,
)
from ..services.orders import search_confirmed_orders


class AlmohitOnlinePaymentController(http.Controller):

    @http.route(
        "/almohit/api/payment/providers",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_providers(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        amount = 0.0
        currency = request.env.company.currency_id
        invoice_id = kwargs.get("invoice_id")
        if invoice_id not in (None, ""):
            orders = search_confirmed_orders(request.env, partner)
            invoice = owned_customer_invoice(orders, invoice_id)
            if not invoice:
                return error("INVOICE_NOT_FOUND", "Invoice not found.", 404)
            amount = invoice.amount_residual
            currency = invoice.currency_id or currency
        providers = compatible_providers(request.env, partner, amount, currency)
        return success([serialize_provider(provider) for provider in providers])

    @http.route(
        "/almohit/api/payment/transactions",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_transactions(self, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        txs = transactions_for_invoices(invoices)
        return success([
            serialize_transaction(tx, invoices)
            for tx in txs
        ])

    @http.route(
        "/almohit/api/payment/transactions/<int:transaction_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_transaction(self, transaction_id, **kwargs):
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        tx = owned_customer_transaction(invoices, transaction_id)
        if not tx:
            return error("TRANSACTION_NOT_FOUND", "Transaction not found.", 404)
        return success(serialize_transaction(tx, invoices))
