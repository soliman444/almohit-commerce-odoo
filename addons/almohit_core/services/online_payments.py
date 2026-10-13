from odoo.http import request
from odoo.tools import float_is_zero

from .api import error
from .customer import commercial_partner
from .payments import _round_amount

ACTIVE_TX_STATES = ("draft", "pending", "authorized")
PAID_STATES = ("paid",)
UNPAYABLE_PAYMENT_STATES = ("reversed", "invoicing_legacy")
HIDDEN_FLOW_KEYS = frozenset({
    "api_key",
    "secret",
    "password",
    "private_key",
    "webhook_secret",
    "webhook_key",
    "signing_secret",
    "access_token",
    "publishable_key",
    "s2s_token",
})
HIDDEN_FLOW_FRAGMENTS = (
    "webhook",
    "private_key",
    "password",
    "signing_secret",
    "api_key",
)


def serialize_provider(provider):
    return {
        "id": provider.id,
        "code": provider.code,
        "name": provider.name,
        "state": provider.state,
    }


def serialize_currency(currency):
    if not currency:
        return None
    return {
        "id": currency.id,
        "name": currency.name,
    }


def _json_amount(currency, amount):
    return _round_amount(currency, amount)


def related_tx_invoices(tx, invoices):
    linked = (tx.invoice_ids & invoices).filtered(
        lambda move: move.move_type == "out_invoice"
    ).sorted("id")
    return [
        {
            "id": invoice.id,
            "name": invoice.name,
        }
        for invoice in linked
    ]


def serialize_transaction(tx, invoices, include_flow=False):
    provider = tx.provider_id
    data = {
        "id": tx.id,
        "reference": tx.reference or "",
        "state": tx.state,
        "amount": _json_amount(tx.currency_id, tx.amount),
        "currency": serialize_currency(tx.currency_id),
        "provider": (
            {
                "id": provider.id,
                "code": provider.code,
                "name": provider.name,
            }
            if provider
            else None
        ),
        "invoices": related_tx_invoices(tx, invoices),
    }
    if include_flow:
        data["transaction_id"] = tx.id
        data["payment_flow"] = payment_flow(tx)
    return data


def payment_flow(tx):
    """Native processing values with secrets stripped.

    Browser redirect/HTML is not proof of payment. Settlement still requires
    the provider's verified notification path.
    """
    tx.ensure_one()
    secret_keys = set(tx._get_specific_secret_keys() or [])
    secret_keys.update(HIDDEN_FLOW_KEYS)
    processing = tx._get_processing_values()
    safe = {}
    for key, value in processing.items():
        lowered = str(key).lower()
        if key in secret_keys or any(part in lowered for part in HIDDEN_FLOW_FRAGMENTS):
            continue
        if key == "redirect_form_html" and value:
            safe[key] = str(value)
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, dict)):
            safe[key] = value
    return {
        "operation": tx.operation,
        "processing": safe,
    }


def compatible_providers(env, partner, amount, currency):
    company = env.company
    currency = currency or company.currency_id
    return env["payment.provider"].sudo()._get_compatible_providers(
        company_id=company.id,
        partner_id=commercial_partner(partner).id,
        amount=amount or 0.0,
        currency_id=currency.id if currency else None,
    )


def allowed_provider(env, partner, provider_id, amount, currency):
    try:
        provider_id = int(provider_id)
    except (TypeError, ValueError):
        return env["payment.provider"].browse()
    providers = compatible_providers(env, partner, amount, currency)
    return providers.filtered(lambda rec: rec.id == provider_id)[:1]


def _primary_payment_method(provider):
    methods = provider.payment_method_ids.filtered("active")
    primary = methods.filtered("is_primary")
    return (primary or methods)[:1]


def _tx_operation(provider):
    if provider._should_build_inline_form():
        return "online_direct"
    return "online_redirect"


def invoice_payment_error(invoice):
    if invoice.state != "posted":
        return error(
            "INVOICE_NOT_PAYABLE",
            "Only posted invoices can be paid online.",
            400,
        )
    if invoice.move_type != "out_invoice":
        return error(
            "INVOICE_NOT_PAYABLE",
            "Only customer invoices can be paid online.",
            400,
        )
    if invoice.payment_state in UNPAYABLE_PAYMENT_STATES:
        return error(
            "INVOICE_NOT_PAYABLE",
            "This invoice cannot be paid online.",
            400,
        )
    residual = invoice.amount_residual
    currency = invoice.currency_id
    if invoice.payment_state in PAID_STATES or (
        currency and currency.is_zero(residual)
    ) or (not currency and float_is_zero(residual, 2)):
        return error(
            "INVOICE_ALREADY_PAID",
            "This invoice is already paid.",
            400,
        )
    if residual <= 0:
        return error(
            "INVOICE_ALREADY_PAID",
            "This invoice is already paid.",
            400,
        )
    return None


def _active_transactions(invoice, provider, amount):
    currency = invoice.currency_id
    txs = invoice.sudo().transaction_ids.filtered(
        lambda tx: (
            tx.provider_id == provider
            and tx.state in ACTIVE_TX_STATES
            and tx.operation != "refund"
            and currency.compare_amounts(tx.amount, amount) == 0
        )
    )
    return txs.sorted("id")


def _collapse_duplicate_active(txs):
    if len(txs) <= 1:
        return txs[:1]
    preferred = txs.filtered(lambda tx: tx.state in ("pending", "authorized"))[:1]
    keep = preferred or txs[:1]
    extras = txs - keep
    extras._set_canceled("Duplicate storefront payment attempt.")
    return keep


def _create_invoice_transaction(invoice, provider, partner):
    method = _primary_payment_method(provider)
    if not method:
        return invoice.env["payment.transaction"].browse(), error(
            "PROVIDER_NOT_AVAILABLE",
            "This payment provider is not available.",
            400,
        )
    residual = invoice.amount_residual
    tx = invoice.env["payment.transaction"].sudo().create({
        "provider_id": provider.id,
        "payment_method_id": method.id,
        "amount": residual,
        "currency_id": invoice.currency_id.id,
        "partner_id": invoice.partner_id.id,
        "operation": _tx_operation(provider),
        "tokenize": False,
        "invoice_ids": [(6, 0, [invoice.id])],
    })
    tx._log_sent_message()
    return tx, None


def start_invoice_payment(partner, invoice, provider_id):
    pay_err = invoice_payment_error(invoice)
    if pay_err:
        return None, pay_err
    residual = invoice.amount_residual
    provider = allowed_provider(
        invoice.env,
        partner,
        provider_id,
        residual,
        invoice.currency_id,
    )
    if not provider:
        return None, error(
            "PROVIDER_NOT_AVAILABLE",
            "This payment provider is not available.",
            400,
        )
    existing = _active_transactions(invoice, provider, residual)
    if existing:
        tx = _collapse_duplicate_active(existing)
        return tx, None
    tx, create_err = _create_invoice_transaction(invoice, provider, partner)
    if create_err:
        return None, create_err
    remaining = _active_transactions(invoice, provider, residual)
    tx = _collapse_duplicate_active(remaining) or tx
    return tx, None


def transactions_for_invoices(invoices):
    if not invoices:
        return invoices.env["payment.transaction"]
    invoices = invoices.sudo().filtered(lambda move: move.move_type == "out_invoice")
    return invoices.transaction_ids.filtered(
        lambda tx: tx.operation != "refund"
    ).sorted("id", reverse=True)


def owned_customer_transaction(invoices, transaction_id):
    try:
        transaction_id = int(transaction_id)
    except (TypeError, ValueError):
        return invoices.env["payment.transaction"].browse()
    return transactions_for_invoices(invoices).filtered(
        lambda tx: tx.id == transaction_id
    )[:1]
