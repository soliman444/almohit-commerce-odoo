from odoo.fields import Command
from odoo import fields


def _receivable_lines(moves):
    return moves.line_ids.filtered(
        lambda line: line.account_id.account_type == "asset_receivable"
    )


def _round_amount(currency, amount):
    if currency:
        rounded = currency.round(amount)
        digits = currency.decimal_places or 2
        return float(format(rounded, f".{digits}f"))
    return float(format(amount, ".2f"))


def _applied_partials(payment, invoice):
    invoice_lines = _receivable_lines(invoice)
    payment_lines = _receivable_lines(payment.move_id)
    if not invoice_lines or not payment_lines:
        return invoice.env["account.partial.reconcile"]
    partials = invoice_lines.matched_debit_ids | invoice_lines.matched_credit_ids
    return partials.filtered(
        lambda part: (
            part.debit_move_id in payment_lines
            or part.credit_move_id in payment_lines
        )
    )


def partial_applied_to_invoice(partial, invoice):
    """Invoice-currency amount of this single partial reconcile.

    Uses debit_amount_currency / credit_amount_currency on the invoice
    receivable line. Not partial.amount (company currency) and not
    invoice.amount_total - invoice.amount_residual.
    """
    invoice_lines = _receivable_lines(invoice)
    if partial.debit_move_id in invoice_lines:
        amount = abs(partial.debit_amount_currency or 0.0)
    elif partial.credit_move_id in invoice_lines:
        amount = abs(partial.credit_amount_currency or 0.0)
    else:
        return 0.0
    return _round_amount(invoice.currency_id, amount)


def partial_is_customer_payment(partial, invoice):
    """True when this partial applies a posted inbound customer payment."""
    invoice_lines = _receivable_lines(invoice)
    if partial.debit_move_id in invoice_lines:
        counterpart = partial.credit_move_id
    elif partial.credit_move_id in invoice_lines:
        counterpart = partial.debit_move_id
    else:
        return False
    payment = counterpart.payment_id or counterpart.move_id.payment_id
    return bool(
        payment
        and payment.state == "posted"
        and payment.payment_type == "inbound"
        and payment.partner_type == "customer"
    )


def applied_amount_to_invoice(payment, invoice):
    """Invoice-currency amount of this payment applied to this invoice.

    Uses account.partial.reconcile debit/credit_amount_currency on the
    invoice receivable line. Not payment.amount (which may include credit).
    """
    invoice_lines = _receivable_lines(invoice)
    total = 0.0
    for part in _applied_partials(payment, invoice):
        if part.debit_move_id in invoice_lines:
            total += part.debit_amount_currency
        else:
            total += part.credit_amount_currency
    return _round_amount(invoice.currency_id or payment.currency_id, total)


def unapplied_amount(payment):
    """Outstanding residual on the payment receivable lines.

    Native leftover credit after reconcile. Not payment.amount minus
    storefront applied (which would mislabel amounts applied elsewhere).
    """
    residual = sum(
        abs(line.amount_residual_currency)
        for line in _receivable_lines(payment.move_id)
    )
    return _round_amount(payment.currency_id, residual)


def payments_for_invoices(invoices):
    """Posted inbound customer payments reconciled to these invoices.

    Starts from invoice receivable lines, walks account.partial.reconcile
    counterpart move lines, then account.payment. Does not search payments
    by partner_id. Draft/cancelled payments are excluded.
    """
    if not invoices:
        return invoices.env["account.payment"]
    invoices = invoices.sudo().filtered(lambda move: move.move_type == "out_invoice")
    Payment = invoices.env["account.payment"]
    receivable = _receivable_lines(invoices)
    counterparts = (
        receivable.matched_debit_ids.debit_move_id
        | receivable.matched_debit_ids.credit_move_id
        | receivable.matched_credit_ids.debit_move_id
        | receivable.matched_credit_ids.credit_move_id
    ) - receivable
    payments = counterparts.payment_id | counterparts.move_id.payment_id
    return payments.filtered(
        lambda payment: (
            payment.state == "posted"
            and payment.payment_type == "inbound"
            and payment.partner_type == "customer"
        )
    ).sorted("id", reverse=True)


def related_invoices(payment, invoices):
    linked = (payment.reconciled_invoice_ids & invoices).filtered(
        lambda move: move.move_type == "out_invoice"
    ).sorted("id")
    rows = []
    for invoice in linked:
        applied = applied_amount_to_invoice(payment, invoice)
        currency = invoice.currency_id or payment.currency_id
        if currency and currency.is_zero(applied):
            continue
        rows.append({
            "id": invoice.id,
            "name": invoice.name,
            "applied_amount": applied,
        })
    return rows


def serialize_payment(payment, invoices):
    currency = payment.currency_id
    journal = payment.journal_id
    applied_invoices = related_invoices(payment, invoices)
    return {
        "id": payment.id,
        "reference": payment.name or "",
        "date": payment.date or None,
        "state": payment.state,
        "payment_type": payment.payment_type,
        "payment_amount": payment.amount,
        "unapplied_amount": unapplied_amount(payment),
        "currency": (
            {
                "id": currency.id,
                "name": currency.name,
            }
            if currency
            else None
        ),
        "journal": (
            {
                "id": journal.id,
                "name": journal.name,
                "type": journal.type,
            }
            if journal
            else None
        ),
        "invoices": applied_invoices,
    }


def serialize_payment_summary(payment, invoice):
    return {
        "id": payment.id,
        "date": payment.date or None,
        "applied_amount": applied_amount_to_invoice(payment, invoice),
        "state": payment.state,
    }


def owned_customer_payment(invoices, payment_id):
    try:
        payment_id = int(payment_id)
    except (TypeError, ValueError):
        return invoices.env["account.payment"].browse()
    return payments_for_invoices(invoices).filtered(
        lambda payment: payment.id == payment_id
    )[:1]


def storefront_customer_invoices(order):
    """Native customer invoices on a sale order. Not a stored paid flag."""
    return order.sudo().invoice_ids.filtered(
        lambda move: move.move_type == "out_invoice" and move.state != "cancel"
    )


def live_posted_invoices(invoices):
    posted = invoices.filtered(lambda move: move.state == "posted")
    return posted.filtered(
        lambda move: move.payment_state not in ("reversed", "invoicing_legacy")
    )


def _line_residual_amount(line):
    if line.currency_id:
        return abs(line.amount_residual_currency)
    return abs(line.amount_residual)


def _line_is_settled(line):
    residual = _line_residual_amount(line)
    currency = line.currency_id or line.company_currency_id
    if currency:
        return bool(currency.is_zero(residual))
    return not residual


def due_now_receivable_lines(invoices, today=None):
    """Receivable maturity lines due today or earlier. Native date_maturity."""
    if not invoices:
        return invoices.env["account.move.line"]
    today = today or fields.Date.context_today(invoices)
    return _receivable_lines(invoices).filtered(
        lambda line: not line.date_maturity or line.date_maturity <= today
    )


def due_now_residual(invoices, today=None):
    lines = due_now_receivable_lines(invoices, today)
    if not lines:
        return 0.0
    currency = lines[:1].currency_id or invoices[:1].currency_id
    total = sum(_line_residual_amount(line) for line in lines)
    return _round_amount(currency, total)


def next_due_date(invoices, today=None):
    if not invoices:
        return None
    today = today or fields.Date.context_today(invoices)
    future = _receivable_lines(invoices).filtered(
        lambda line: (
            line.date_maturity
            and line.date_maturity > today
            and not _line_is_settled(line)
        )
    )
    dates = [line.date_maturity for line in future if line.date_maturity]
    return min(dates) if dates else None


def invoices_due_now_settled(invoices, today=None):
    due = due_now_receivable_lines(invoices, today)
    if not due:
        return True
    return all(_line_is_settled(line) for line in due)


def invoice_due_snapshot(move, today=None):
    """Native amounts for WhatsApp. Not a stored schedule."""
    currency = move.currency_id
    return {
        "amount_total": _round_amount(currency, move.amount_total or 0.0),
        "due_now": due_now_residual(move, today),
        "residual": _round_amount(currency, move.amount_residual or 0.0),
        "next_due_date": next_due_date(move, today),
        "payment_term": (
            move.invoice_payment_term_id.name if move.invoice_payment_term_id else ""
        ),
        "currency": currency.name if currency else "",
    }


def order_allows_distribution(order, today=None):
    """Allow distribution when every currently-due receivable is settled.

    Uses native account.move.line date_maturity and amount_residual_currency
    on posted customer invoices. Future-dated payment-term portions do not
    block. payment_state == paid is not required.
    """
    if not order:
        return False
    live = live_posted_invoices(storefront_customer_invoices(order))
    if not live:
        return False
    return invoices_due_now_settled(live, today)


def _create_invoice_from_ordered_qty(order):
    """Regular invoice from ordered qty using native prepare methods.

    Storefront products may use the delivery invoicing policy. Payment terms
    still need a posted invoice at confirm, without faking stock delivery.
    """
    lines = order.order_line.filtered(
        lambda line: not line.display_type and line.product_uom_qty
    )
    if not lines:
        return order.env["account.move"]
    values = order.sudo()._prepare_invoice()
    values["invoice_line_ids"] = [
        Command.create(line._prepare_invoice_line(quantity=line.product_uom_qty))
        for line in lines
    ]
    return order.env["account.move"].sudo().create(values)


def create_storefront_invoice(order):
    """Create and post a regular native invoice so payment terms apply.

    Prefers sale.order._create_invoices. If nothing is invoiceable yet
    (delivery policy), invoices ordered quantities through native prepare
    methods. Does not use sale.advance.payment.inv.
    """
    if not order or not order.almohit_storefront_order:
        return order.env["account.move"]
    if order.state not in ("sale", "done"):
        return order.env["account.move"]
    existing = storefront_customer_invoices(order)
    if existing:
        drafts = existing.filtered(lambda move: move.state == "draft")
        if drafts:
            drafts._post(soft=False)
        return existing[:1]
    invoices = order.env["account.move"]
    if order._get_invoiceable_lines():
        invoices = order.sudo()._create_invoices()
    if not invoices:
        invoices = _create_invoice_from_ordered_qty(order)
    drafts = invoices.filtered(lambda move: move.state != "posted")
    if drafts:
        drafts._post(soft=False)
    return invoices


def serialize_order_payment(invoices):
    """Derive order payment display from linked out_invoices. Not stored.

    Customer-facing states: null, not_paid, partial, paid, reversed.

    Native invoice payment_state values:
    not_paid, in_payment, paid, partial, reversed, invoicing_legacy.

    Live invoices exclude reversed and invoicing_legacy so a reversal is
    never counted as paid or partial:

    - no posted invoices → null
    - posted only reversed/legacy → reversed if any reversed else null
    - every live posted invoice paid → paid
    - every live posted invoice not_paid → not_paid
    - any live mix (partial, in_payment, paid+unpaid) → partial
    """
    active = invoices.filtered(
        lambda move: move.state != "cancel" and move.move_type == "out_invoice"
    )
    posted = active.filtered(lambda move: move.state == "posted")
    if not posted:
        return {
            "invoice_count": len(active),
            "state": None,
        }
    reversed_moves = posted.filtered(lambda move: move.payment_state == "reversed")
    live = posted.filtered(
        lambda move: move.payment_state not in ("reversed", "invoicing_legacy")
    )
    if not live:
        return {
            "invoice_count": len(active),
            "state": "reversed" if reversed_moves else None,
        }
    states = [move.payment_state or "not_paid" for move in live]
    if all(state == "paid" for state in states):
        agg = "paid"
    elif all(state == "not_paid" for state in states):
        agg = "not_paid"
    else:
        agg = "partial"
    return {
        "invoice_count": len(active),
        "state": agg,
    }
