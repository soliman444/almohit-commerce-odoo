from odoo import models

from ..services.invoices import storefront_orders_from_invoice
from ..services.whatsapp import (
    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
    EVENT_INVOICE_POSTED,
    schedule_notification,
)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _almohit_storefront_invoice_order(self):
        self.ensure_one()
        if self.move_type != "out_invoice":
            return self.env["sale.order"]
        return storefront_orders_from_invoice(self)[:1]

    def _post(self, soft=True):
        pending = self.filtered(lambda move: move.state != "posted")

        result = super()._post(soft=soft)

        posted = result if isinstance(result, models.BaseModel) else self

        for move in posted.filtered(
            lambda rec: rec.id in pending.ids and rec.state == "posted"
        ):
            order = move._almohit_storefront_invoice_order()
            if order:
                schedule_notification(
                    EVENT_INVOICE_POSTED,
                    move,
                    extra={"order": order},
                )

                schedule_notification(
                    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
                    order,
                )

        return result