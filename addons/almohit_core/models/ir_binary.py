from odoo import models


class IrBinary(models.AbstractModel):
    """Public storefront fetches /web/image/product.template/<id>/image_256
    with no session. Public users cannot read product.template, so Odoo would
    otherwise serve the placeholder camera PNG instead of image_1920.
    """

    _inherit = "ir.binary"

    def _find_record_check_access(self, record, access_token):
        if record._name in ("product.template", "product.product"):
            sudoed = record.sudo()
            if sudoed.exists() and sudoed.active and sudoed.sale_ok:
                return sudoed
        if record._name == "product.image":
            sudoed = record.sudo()
            product = sudoed.product_variant_id or sudoed.product_tmpl_id
            if product and product.exists() and product.active and product.sale_ok:
                return sudoed
        if record._name == "almohit.product.brand":
            sudoed = record.sudo()
            if sudoed.exists() and sudoed.active:
                return sudoed
        return super()._find_record_check_access(record, access_token)
