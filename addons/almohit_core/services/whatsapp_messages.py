"""WhatsApp message rendering. Isolated from event detection and HTTP send.

render_message() is the only entry point used by schedule_notification.
Model hooks stay unaware of templates.
"""

import logging

from .payments import invoice_due_snapshot
from .invoices import partner_outstanding_balance
_logger = logging.getLogger(__name__)


def format_amount(amount):
    try:
        return "%.2f" % float(amount)
    except (TypeError, ValueError):
        return str(amount)


def format_partner_address(partner):
    if not partner:
        return ""
    parts = [
        partner.street or "",
        partner.street2 or "",
        partner.city or "",
        partner.state_id.name if partner.state_id else "",
        partner.country_id.name if partner.country_id else "",
    ]
    return "، ".join(part for part in parts if part)


def _customer_name(partner, fallback=""):
    if partner and partner.name:
        return partner.name
    return fallback or ""


def _currency_name(record):
    currency = getattr(record, "currency_id", False)
    return currency.name if currency else ""


def _payment_term_name(record):
    term = getattr(record, "payment_term_id", False) or getattr(
        record, "invoice_payment_term_id", False
    )
    return term.name if term else ""


def _trade_approved(record, extra):
    partner = record.partner_id
    name = _customer_name(partner, record.company_name or "")
    return (
        "مرحبًا {name}،\n"
        "تمت الموافقة على حساب الجملة الخاص بك في Almohit.\n"
        "يمكنك الآن تسجيل الدخول والاطلاع على أسعار الجملة."
    ).format(name=name)


def _order_confirmed(record, extra):
    partner = record.partner_id
    lines = [
        "مرحبًا {name}،".format(name=_customer_name(partner)),
        "تم تأكيد طلبك {order}.".format(order=record.name),
        "الإجمالي: {amount} {currency}.".format(
            amount=format_amount(record.amount_total),
            currency=_currency_name(record),
        ),
    ]
    term = _payment_term_name(record)
    if term:
        lines.append("شروط الدفع: {term}.".format(term=term))
    return "\n".join(lines)


def _invoice_posted(record, extra):
    order = extra.get("order")
    partner = order.partner_id if order else record.partner_id
    snapshot = invoice_due_snapshot(record)
    currency = record.currency_id
    due_now = snapshot["due_now"]
    residual = snapshot["residual"]
    due_now_is_zero = bool(currency.is_zero(due_now)) if currency else not due_now
    later = max(residual - due_now, 0.0)
    has_later_balance = not (bool(currency.is_zero(later)) if currency else not later)
    next_due = snapshot["next_due_date"]
    lines = [
        "مرحبًا {name}،".format(name=_customer_name(partner)),
        "تم إصدار الفاتورة {invoice}.".format(invoice=record.name),
        "إجمالي الفاتورة: {amount} {currency}.".format(
            amount=format_amount(snapshot["amount_total"]),
            currency=snapshot["currency"],
        ),
    ]
    if due_now_is_zero:
        term = snapshot["payment_term"]
        if term:
            lines.append("شروط الدفع: {term}.".format(term=term))
        lines.append("لا يوجد مبلغ مستحق حاليًا قبل التوزيع.")
        if next_due:
            lines.append("تاريخ الاستحقاق القادم: {due}.".format(due=next_due))
    elif has_later_balance:
        lines.append(
            "المبلغ المستحق حاليًا: {amount} {currency}.".format(
                amount=format_amount(due_now),
                currency=snapshot["currency"],
            )
        )
        term = snapshot["payment_term"]
        if term:
            lines.append(
                "الرصيد المتبقي يستحق لاحقًا حسب شروط الدفع {term}.".format(term=term)
            )
        else:
            lines.append("الرصيد المتبقي يستحق لاحقًا حسب شروط الدفع.")
    else:
        lines.append(
            "المبلغ المستحق حاليًا: {amount} {currency}.".format(
                amount=format_amount(due_now),
                currency=snapshot["currency"],
            )
        )
        lines.append("يجب سداد المبلغ المستحق حاليًا قبل تحويل الطلب إلى التوزيع.")
    lines.append("يمكنك مراجعة الفاتورة وإتمام السداد من حسابك في Almohit.")
    return "\n".join(lines)


def _payment_received(record, extra):
    extra = extra or {}
    partner = record.partner_id
    outstanding = partner_outstanding_balance(
        partner,
        company=record.company_id,
    )
    return (
        "مرحبًا {name}،\n"
        "\n"
        "تم استلام دفعة بقيمة {amount} {currency}\n"
        "للفاتورة {invoice}.\n"
        "\n"
        "المتبقي على الفاتورة:\n"
        "{residual} {currency}.\n"
        "\n"
        "إجمالي الرصيد غير المسدد على حسابكم:\n"
        "{outstanding} {currency}."
    ).format(
        name=_customer_name(partner),
        amount=format_amount(extra.get("applied_amount") or 0),
        currency=_currency_name(record),
        invoice=record.name,
        residual=format_amount(record.amount_residual),
        outstanding=format_amount(outstanding),
    )


def _invoice_paid(record, extra):
    return (
        "تم استلام كامل قيمة الفاتورة {invoice}.\n"
        "المبلغ المتبقي: 0 {currency}."
    ).format(invoice=record.name, currency=_currency_name(record))


def _distribution_requirement_satisfied(record, extra):
    return (
        "تم استيفاء المبلغ المستحق حاليًا للطلب {order}.\n"
        "أصبح الطلب جاهزًا للإسناد إلى الموزع."
    ).format(order=record.name)


def _distribution_assigned(record, extra):
    order = extra.get("order")
    customer = order.partner_id if order else None
    return (
        "تم إسناد طلب جديد إليك للتوزيع.\n"
        "\n"
        "رقم الطلب: {order}\n"
        "العميل: {customer}\n"
        "عنوان التوصيل: {address}\n"
        "إجمالي الطلب: {amount} {currency}\n"
        "حالة السداد: المبلغ المستحق حاليًا مستوفى\n"
        "\n"
        "يرجى البدء في تجهيز وتسليم الطلب."
    ).format(
        order=order.name if order else "",
        customer=_customer_name(customer),
        address=format_partner_address(order.partner_shipping_id if order else None) or "—",
        amount=format_amount(order.amount_total) if order else "0.00",
        currency=_currency_name(order) if order else "",
    )


def _delivery_done(record, extra):
    order = extra.get("order")
    return (
        "تم تسليم الطلب {order} بنجاح.\n"
        "مرجع التسليم: {picking}."
    ).format(
        order=order.name if order else "",
        picking=record.name,
    )


_RENDERERS = {
    "trade_approved": _trade_approved,
    "order_confirmed": _order_confirmed,
    "invoice_posted": _invoice_posted,
    "distribution_payment_requirement_satisfied": _distribution_requirement_satisfied,
    "payment_received": _payment_received,
    "invoice_paid": _invoice_paid,
    "distribution_assigned": _distribution_assigned,
    "delivery_done": _delivery_done,
}


class RenderResult:
    """Result of render_message(). skip=True means do not send and do not fallback."""

    def __init__(
        self,
        text="",
        skip=False,
        skip_reason="",
        source="fallback",
        template_id=False,
        error="",
    ):
        self.text = text or ""
        self.skip = bool(skip)
        self.skip_reason = skip_reason or ""
        self.source = source or "fallback"
        self.template_id = template_id or False
        self.error = error or ""


def order_for_record(event_code, record, extra=None):
    extra = extra or {}
    order = extra.get("order")
    if order:
        return order
    if getattr(record, "_name", "") == "sale.order":
        return record
    if getattr(record, "_name", "") == "stock.picking":
        return record.sale_id
    if getattr(record, "_name", "") == "account.move":
        from .invoices import storefront_orders_from_invoice

        return storefront_orders_from_invoice(record)[:1]
    return None


def extra_context(event_code, record, extra=None):
    """Safe helper values already computed in Python. Templates only interpolate them."""
    extra = extra or {}
    order = order_for_record(event_code, record, extra)
    context = {}
    if event_code == "invoice_posted":
        snapshot = invoice_due_snapshot(record)
        outstanding = partner_outstanding_balance(
            record.partner_id,
            company=record.company_id,
        )
        context["due_now"] = format_amount(snapshot["due_now"])
        context["next_due_date"] = str(snapshot["next_due_date"] or "")
        context["payment_term_name"] = snapshot["payment_term"] or ""
        context["outstanding_balance"] = format_amount(outstanding)
    if event_code == "payment_received":
        outstanding = partner_outstanding_balance(
            record.partner_id,
            company=record.company_id,
        )
        context["payment_amount"] = format_amount(extra.get("applied_amount") or 0)
        context["invoice_residual"] = format_amount(record.amount_residual)
        context["outstanding_balance"] = format_amount(outstanding)
    if event_code == "distribution_assigned":
        customer = order.partner_id if order else None
        context["order_name"] = order.name if order else ""
        context["customer_name"] = _customer_name(customer)
        context["shipping_address"] = (
            format_partner_address(order.partner_shipping_id if order else None) or "—"
        )
        context["amount_total"] = format_amount(order.amount_total) if order else "0.00"
        context["currency"] = _currency_name(order) if order else ""
    if event_code == "delivery_done":
        customer = order.partner_id if order else None
        context["order_name"] = order.name if order else ""
        context["customer_name"] = _customer_name(customer)
    return context


def render_fallback(event_code, record, extra=None):
    renderer = _RENDERERS.get(event_code)
    if not renderer or not record:
        return ""
    return renderer(record, extra or {})


def render_message(event_code, record, extra=None):
    """Canonical renderer: template → skip/disabled → fallback. Never raises."""
    extra = extra or {}
    if not record or event_code not in _RENDERERS:
        return RenderResult()
    try:
        Template = record.env["almohit.notification.template"].sudo()
        return Template.render_event(event_code, record, extra)
    except KeyError:
        return RenderResult(text=render_fallback(event_code, record, extra), source="fallback")
    except Exception:
        _logger.exception(
            "whatsapp render_message failed event=%s model=%s res_id=%s",
            event_code,
            getattr(record, "_name", None),
            getattr(record, "id", None),
        )
        return RenderResult(
            text=render_fallback(event_code, record, extra),
            source="fallback",
            error="RENDER_ERROR",
        )
