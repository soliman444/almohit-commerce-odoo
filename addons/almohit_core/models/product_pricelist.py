from odoo import fields, models


class ProductPricelist(models.Model):
    _inherit = "product.pricelist"

    almohit_currency_variant_ids = fields.Many2many(
        "product.pricelist",
        "almohit_pricelist_currency_variant_rel",
        "pricelist_id",
        "variant_id",
        string="Almohit Currency Variants",
        help=(
            "Authorized same-segment pricelists in other currencies for the "
            "Almohit storefront. Merchants may only switch to these linked lists."
        ),
        domain="[('id', '!=', id)]",
    )
