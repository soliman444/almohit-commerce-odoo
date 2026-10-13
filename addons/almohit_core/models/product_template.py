from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    almohit_brand_id = fields.Many2one(
        "almohit.product.brand",
        string="Brand",
        index=True,
        ondelete="set null",
    )
    almohit_min_order_qty = fields.Float(
        string="Minimum Order Quantity",
        default=1.0,
        required=True,
        help="Minimum quantity a customer must order for this product.",
    )
    product_template_image_ids = fields.One2many(
        "product.image",
        "product_tmpl_id",
        string="Extra Product Media",
        copy=True,
    )
   