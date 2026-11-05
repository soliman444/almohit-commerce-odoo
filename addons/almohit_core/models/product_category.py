
from odoo import api, fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    image_1920 = fields.Image(
        string="Category Image",
        max_width=1920,
        max_height=1920,
    )

    def _almohit_sync_image_public(self):
        """Expose only category image attachments publicly."""
        Attachment = self.env["ir.attachment"].sudo()

        for category in self:
            attachments = Attachment.search([
                ("res_model", "=", "product.category"),
                ("res_id", "=", category.id),
                ("res_field", "=", "image_1920"),
            ])

            if attachments:
                attachments.write({
                    "public": bool(category.image_1920),
                })

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._almohit_sync_image_public()
        return records

    def write(self, vals):
        result = super().write(vals)

        if "image_1920" in vals:
            self._almohit_sync_image_public()

        return result
