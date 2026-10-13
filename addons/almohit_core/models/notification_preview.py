from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.whatsapp_messages import order_for_record


class AlmohitNotificationPreview(models.TransientModel):
    _name = "almohit.notification.preview"
    _description = "Preview WhatsApp Notification Template"

    template_id = fields.Many2one(
        "almohit.notification.template",
        required=True,
        ondelete="cascade",
    )
    event_code = fields.Selection(related="template_id.event_code", readonly=True)
    model_name = fields.Char(related="template_id.model_name", readonly=True)
    application_id = fields.Many2one("almohit.trade.application", string="Trade Application")
    order_id = fields.Many2one("sale.order", string="Sales Order")
    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        domain=[("move_type", "=", "out_invoice")],
    )
    picking_id = fields.Many2one("stock.picking", string="Transfer")
    preview_text = fields.Text(readonly=True)
    error = fields.Char(readonly=True)

    @api.onchange("template_id")
    def _onchange_template_id(self):
        self.preview_text = False
        self.error = False

    def _preview_record(self):
        self.ensure_one()
        model_name = self.model_name
        mapping = {
            "almohit.trade.application": self.application_id,
            "sale.order": self.order_id,
            "account.move": self.invoice_id,
            "stock.picking": self.picking_id,
        }
        record = mapping.get(model_name)
        if not record:
            raise UserError("Select a record of model %s." % (model_name or "the template"))
        return record

    def action_render(self):
        self.ensure_one()
        record = self._preview_record()
        extra = {}
        order = order_for_record(self.event_code, record, extra)
        if order:
            extra["order"] = order
        try:
            self.preview_text = self.template_id.render_body(record, extra)
            self.error = False
        except Exception as error:
            self.preview_text = False
            self.error = str(error)
        return {
            "type": "ir.actions.act_window",
            "name": "Preview WhatsApp Message",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
