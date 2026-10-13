from odoo import fields, models


class AlmohitProductBrand(models.Model):
    _name = "almohit.product.brand"
    _description = "Almohit Product Brand"
    _inherit = ["image.mixin"]
    _order = "name, id"

    name = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
