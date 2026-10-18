from odoo import http
from odoo.http import request

from ..services.api import error, paginated_success, parse_pagination, read_json_body, success
from ..services.customer import require_customer_partner, require_merchant_group
from ..services.invoices import owned_customer_invoice, search_customer_invoices
from ..services.online_payments import (
    compatible_providers,
    owned_customer_transaction,
    parse_transaction_list_params,
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
        err = require_merchant_group()
        if err:
            return err
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
        err = require_merchant_group()
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        page, page_size, err = parse_pagination(kwargs)
        if err:
            return err
        extra, order, err = parse_transaction_list_params(kwargs)
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        txs = transactions_for_invoices(invoices)
        txs = txs.search(
            [("id", "in", txs.ids)] + extra,
            order=order,
        )
        return paginated_success(
            txs,
            lambda tx: serialize_transaction(tx, invoices),
            page,
            page_size,
        )

    @http.route(
        "/almohit/api/payment/transactions/<int:transaction_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_transaction(self, transaction_id, **kwargs):
        err = require_merchant_group()
        if err:
            return err
        partner, err = require_customer_partner()
        if err:
            return err
        orders = search_confirmed_orders(request.env, partner)
        invoices = search_customer_invoices(orders)
        tx = owned_customer_transaction(invoices, transaction_id)
        if not tx:
            return error("TRANSACTION_NOT_FOUND", "Transaction not found.", 404)
        return success(serialize_transaction(tx, invoices))
