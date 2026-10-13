from .payments import payments_for_invoices, serialize_payment_summary


def serialize_invoice_line(line):
    product = line.product_id
    uom = line.product_uom_id
    return {
        "product_id": product.id if product else None,
        "name": line.name or "",
        "quantity": line.quantity,
        "uom": (
            {
                "id": uom.id,
                "name": uom.name,
            }
            if uom
            else None
        ),
        "unit_price": line.price_unit,
        "subtotal": line.price_subtotal,
    }


def storefront_orders_from_invoice(move):
    return move.invoice_line_ids.sale_line_ids.order_id.filtered(
        lambda order: order.almohit_storefront_order
    )


def related_storefront_order(move):
    order = storefront_orders_from_invoice(move)[:1]
    if not order:
        return None
    return {
        "id": order.id,
        "name": order.name,
    }


def serialize_invoice(move):
    currency = move.currency_id
    items = [
        serialize_invoice_line(line)
        for line in move.invoice_line_ids.filtered(
            lambda rec: rec.display_type == "product"
        )
    ]
    payments = payments_for_invoices(move)
    return {
        "id": move.id,
        "name": move.name,
        "state": move.state,
        "payment_state": move.payment_state or None,
        "invoice_date": move.invoice_date or None,
        "due_date": move.invoice_date_due or None,
        "currency": (
            {
                "id": currency.id,
                "name": currency.name,
            }
            if currency
            else None
        ),
        "amount_untaxed": move.amount_untaxed,
        "amount_tax": move.amount_tax,
        "amount_total": move.amount_total,
        "amount_residual": move.amount_residual,
        "paid_amount": invoice_paid_amount(move),
        "payment_term": (
            {
                "id": move.invoice_payment_term_id.id,
                "name": move.invoice_payment_term_id.name,
            }
            if move.invoice_payment_term_id
            else None
        ),
        "order": related_storefront_order(move),
        "payments": [
            serialize_payment_summary(payment, move) for payment in payments
        ],
        "items": items,
    }


def invoice_paid_amount(move):
    """Serializer-only. amount_total - amount_residual when that is truthful.

    Omitted (null) for reversed/legacy/cancelled invoices: residual can be 0
    without the invoice having been paid. Overpayment leftover lives on the
    payment as partner credit, so this still equals the invoice total when
    payment_state is paid.
    """
    if move.state == "cancel" or move.payment_state in (
        "reversed",
        "invoicing_legacy",
    ):
        return None
    currency = move.currency_id
    paid = move.amount_total - move.amount_residual
    if currency:
        return float(format(currency.round(paid), f".{currency.decimal_places or 2}f"))
    return float(format(paid, ".2f"))


def serialize_invoice_summary(move):
    return {
        "id": move.id,
        "name": move.name,
        "state": move.state,
        "payment_state": move.payment_state or None,
        "amount_total": move.amount_total,
    }


def customer_invoice_ids(orders):
    return orders.mapped("invoice_ids").filtered(
        lambda move: move.move_type == "out_invoice"
    )


def search_customer_invoices(orders):
    return customer_invoice_ids(orders).sorted("id", reverse=True)


def owned_customer_invoice(orders, invoice_id):
    try:
        invoice_id = int(invoice_id)
    except (TypeError, ValueError):
        return orders.env["account.move"].browse()
    return customer_invoice_ids(orders).filtered(lambda move: move.id == invoice_id)[:1]

def partner_outstanding_balance(partner, company=None):
    """Return customer's total unreconciled receivable balance
    in company currency.

    Uses native Odoo receivable journal items.
    """
    if not partner:
        return 0.0

    partner = partner.commercial_partner_id
    company = company or partner.company_id or partner.env.company

    lines = partner.env["account.move.line"].sudo().search([
        ("partner_id", "child_of", partner.id),
        ("company_id", "=", company.id),
        ("parent_state", "=", "posted"),
        ("account_id.account_type", "=", "asset_receivable"),
        ("reconciled", "=", False),
    ])

    return sum(lines.mapped("amount_residual"))