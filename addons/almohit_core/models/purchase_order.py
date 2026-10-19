from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    almohit_purchase_image_ids = fields.Many2many(
        "ir.attachment",
        "almohit_purchase_order_attachment_rel",
        "purchase_order_id",
        "attachment_id",
        string="صور بضاعة الرعوي",
        copy=False,
    )