import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.whatsapp_messages import extra_context, format_amount, render_fallback

_logger = logging.getLogger(__name__)

EVENT_TRADE_APPROVED = "trade_approved"
EVENT_ORDER_CONFIRMED = "order_confirmed"
EVENT_INVOICE_POSTED = "invoice_posted"
EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED = "distribution_payment_requirement_satisfied"
EVENT_PAYMENT_RECEIVED = "payment_received"
EVENT_INVOICE_PAID = "invoice_paid"
EVENT_DISTRIBUTION_ASSIGNED = "distribution_assigned"
EVENT_DELIVERY_DONE = "delivery_done"

EVENT_SELECTION = [
    (EVENT_TRADE_APPROVED, "Trade Approved"),
    (EVENT_ORDER_CONFIRMED, "Order Confirmed"),
    (EVENT_INVOICE_POSTED, "Invoice Posted"),
    (EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED, "Distribution Payment Requirement Satisfied"),
    (EVENT_PAYMENT_RECEIVED, "Payment Received"),
    (EVENT_INVOICE_PAID, "Invoice Paid"),
    (EVENT_DISTRIBUTION_ASSIGNED, "Distribution Assigned"),
    (EVENT_DELIVERY_DONE, "Delivery Done"),
]

EVENT_META = {
    EVENT_TRADE_APPROVED: {
        "model": "almohit.trade.application",
        "recipient_type": "customer",
        "help": (
            "object.partner_id.name — Customer name\n"
            "object.company_name — Company name on the application"
        ),
    },
    EVENT_ORDER_CONFIRMED: {
        "model": "sale.order",
        "recipient_type": "customer",
        "help": (
            "object.name — Order number\n"
            "object.partner_id.name — Customer name\n"
            "object.amount_total — Order total\n"
            "object.currency_id.name — Currency\n"
            "object.payment_term_id.name — Payment terms"
        ),
    },
    EVENT_INVOICE_POSTED: {
        "model": "account.move",
        "recipient_type": "customer",
        "help": (
            "object.name — Invoice number\n"
            "object.partner_id.name — Customer name\n"
            "object.amount_total — Invoice total\n"
            "object.amount_residual — Residual amount\n"
            "object.currency_id.name — Currency\n"
            "object.invoice_payment_term_id.name — Payment terms on the invoice\n"
            "due_now — Amount due now (computed in Python)\n"
            "next_due_date — Next installment date (computed in Python)\n"
            "payment_term_name — Payment term label (computed in Python)"
        ),
    },
    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED: {
        "model": "sale.order",
        "recipient_type": "customer",
        "help": (
            "object.name — Order number\n"
            "object.partner_id.name — Customer name"
        ),
    },
    EVENT_PAYMENT_RECEIVED: {
        "model": "account.move",
        "recipient_type": "customer",
        "help": (
            "object.name — Invoice number\n"
            "object.partner_id.name — Customer name\n"
            "object.currency_id.name — Currency\n"
            "payment_amount — This payment amount (computed in Python)\n"
            "invoice_residual — Remaining amount on this invoice (computed in Python)\n"
            "outstanding_balance — Customer outstanding receivable (computed in Python)"
        ),
    },
    EVENT_INVOICE_PAID: {
        "model": "account.move",
        "recipient_type": "customer",
        "help": (
            "object.name — Invoice number\n"
            "object.partner_id.name — Customer name\n"
            "object.currency_id.name — Currency"
        ),
    },
    EVENT_DISTRIBUTION_ASSIGNED: {
        "model": "stock.picking",
        "recipient_type": "responsible",
        "help": (
            "object.name — Delivery reference\n"
            "object.user_id.name — Assigned responsible\n"
            "object.partner_id.name — Delivery address partner\n"
            "order_name — Related sales order\n"
            "customer_name — Customer name\n"
            "shipping_address — Shipping address\n"
            "amount_total — Order total (formatted)\n"
            "currency — Order currency"
        ),
    },
    EVENT_DELIVERY_DONE: {
        "model": "stock.picking",
        "recipient_type": "customer",
        "help": (
            "object.name — Delivery reference\n"
            "order_name — Related sales order\n"
            "customer_name — Customer name"
        ),
    },
}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_EXPRESSION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_BLOCKED_NAMES = frozenset({
    "env",
    "sudo",
    "pool",
    "cr",
    "_cr",
    "browse",
    "search",
    "search_count",
    "create",
    "write",
    "unlink",
    "read",
    "mapped",
    "filtered",
    "filtered_domain",
    "with_context",
    "with_user",
    "with_company",
    "with_env",
    "invalidate_model",
    "invalidate_recordset",
    "flush_model",
    "flush_recordset",
    "new",
    "concat",
    "exists",
    "__class__",
    "__dict__",
    "__globals__",
    "__code__",
    "request",
    "os",
    "sys",
    "eval",
    "exec",
    "open",
    "globals",
    "locals",
    "_context",
    "context",
    "uid",
})


class AlmohitNotificationTemplate(models.Model):
    _name = "almohit.notification.template"
    _description = "Almohit WhatsApp Notification Template"
    _order = "event_code"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    event_code = fields.Selection(
        selection=EVENT_SELECTION,
        required=True,
        index=True,
    )
    model_name = fields.Char(
        string="Technical Model",
        compute="_compute_event_metadata",
        compute_sudo=True,
        store=True,
        readonly=True,
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        compute="_compute_event_metadata",
        compute_sudo=True,
        store=True,
        readonly=True,
        ondelete="set null",
    )
    recipient_type = fields.Selection(
        selection=[
            ("customer", "Customer"),
            ("responsible", "Assigned Responsible"),
        ],
        compute="_compute_event_metadata",
        compute_sudo=True,
        store=True,
        readonly=True,
    )
    body = fields.Text(string="Message Body", required=True)
    variable_help = fields.Text(
        string="Available Variables",
        compute="_compute_event_metadata",
        compute_sudo=True,
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "event_code_uniq",
            "unique(event_code)",
            "Only one WhatsApp template is allowed per event.",
        ),
    ]

    @api.depends("event_code")
    def _compute_event_metadata(self):
        IrModel = self.env["ir.model"].sudo()
        for template in self:
            meta = EVENT_META.get(template.event_code) or {}
            model_name = meta.get("model") or False
            template.model_name = model_name
            template.recipient_type = meta.get("recipient_type") or False
            template.variable_help = meta.get("help") or ""
            template.model_id = (
                IrModel.search([("model", "=", model_name)], limit=1).id
                if model_name
                else False
            )

    @api.constrains("event_code")
    def _check_event_code(self):
        allowed = {item[0] for item in EVENT_SELECTION}
        for template in self:
            if template.event_code not in allowed:
                raise ValidationError("Unsupported WhatsApp event.")

    def action_open_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Preview WhatsApp Message",
            "res_model": "almohit.notification.preview",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_template_id": self.id,
            },
        }

    @api.model
    def render_event(self, event_code, record, extra=None):
        """Find the event template, render it, or return skip/fallback metadata."""
        from ..services.whatsapp_messages import RenderResult

        extra = extra or {}
        template = self.with_context(active_test=False).search(
            [("event_code", "=", event_code)],
            limit=1,
        )
        if template and not template.active:
            return RenderResult(
                skip=True,
                skip_reason="TEMPLATE_DISABLED",
                source="disabled",
                template_id=template.id,
            )
        if template:
            try:
                text = template.render_body(record, extra)
                if text:
                    return RenderResult(
                        text=text,
                        source="template",
                        template_id=template.id,
                    )
                raise ValueError("Rendered WhatsApp template was empty.")
            except Exception as error:
                _logger.exception(
                    "whatsapp template render failed event=%s template=%s model=%s res_id=%s",
                    event_code,
                    template.id,
                    getattr(record, "_name", None),
                    getattr(record, "id", None),
                )
                fallback = render_fallback(event_code, record, extra)
                return RenderResult(
                    text=fallback or "",
                    source="fallback",
                    template_id=template.id,
                    error=str(error),
                )
        fallback = render_fallback(event_code, record, extra)
        return RenderResult(text=fallback or "", source="fallback")

    def render_body(self, record, extra=None):
        """Safely render this template against a real record. Never sends WhatsApp."""
        self.ensure_one()
        if not record:
            raise UserError("A record is required to render this template.")
        expected_model = self.model_name
        if expected_model and record._name != expected_model:
            raise UserError(
                "This template belongs to %s, not %s."
                % (expected_model, record._name)
            )
        record_sudo = record.sudo()
        extra = extra or {}
        context = extra_context(self.event_code, record_sudo, extra)

        def _replace(match):
            expression = (match.group(1) or "").strip()
            return self._resolve_expression(expression, record_sudo, context)

        return _PLACEHOLDER_RE.sub(_replace, self.body or "").strip()

    def _resolve_expression(self, expression, record, context):
        if not _EXPRESSION_RE.match(expression or ""):
            raise ValueError("Unsupported template expression: %s" % expression)
        parts = expression.split(".")
        if any(part in _BLOCKED_NAMES or part.startswith("_") for part in parts):
            raise ValueError("Blocked template expression: %s" % expression)
        first, rest = parts[0], parts[1:]
        if first == "object":
            current = record
            field_type = None
        elif first in context:
            current = context[first]
            field_type = None
        else:
            raise ValueError("Unknown template variable: %s" % first)
        for part in rest:
            current, field_type = self._read_attr(current, part)
        return self._stringify(current, field_type)

    def _read_attr(self, current, name):
        if current is None or current is False:
            return False, None
        if isinstance(current, models.BaseModel):
            if name not in current._fields:
                raise ValueError("Unknown field %s on %s" % (name, current._name))
            field = current._fields[name]
            value = current[name]
            return value, field.type
        if isinstance(current, dict):
            if name not in current:
                raise ValueError("Unknown key %s" % name)
            return current.get(name), None
        raise ValueError("Cannot read %s on a non-record value" % name)

    def _stringify(self, value, field_type=None):
        if value is None or value is False:
            return ""
        if isinstance(value, models.BaseModel):
            if not value:
                return ""
            return value[:1].display_name or ""
        if field_type in ("float", "monetary") or isinstance(value, float):
            return format_amount(value)
        return str(value)
