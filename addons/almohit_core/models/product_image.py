from odoo import fields, models


class ProductImage(models.Model):
    """Native extra product images (website_sale's product.image is not installed)."""

    _name = "product.image"
    _description = "Product Image"
    _inherit = ["image.mixin"]
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10, index=True)
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Product Template",
        index=True,
        ondelete="cascade",
    )
    product_variant_id = fields.Many2one(
        "product.product",
        string="Product Variant",
        index=True,
        ondelete="cascade",
    )
