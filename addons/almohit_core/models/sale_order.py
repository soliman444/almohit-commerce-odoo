import logging

from odoo import api, fields, models

from ..services.payments import create_storefront_invoice
from ..services.whatsapp import EVENT_ORDER_CONFIRMED, schedule_notification

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    almohit_storefront_order = fields.Boolean(
        string="Almohit Storefront Cart",
        default=False,
        index=True,
        copy=False,
        help="Marks a draft quotation created by the Almohit customer storefront.",
    )

    @api.model
    def _almohit_cart_domain(self, partner):
        return [
            ("partner_id", "=", partner.id),
            ("state", "=", "draft"),
            ("almohit_storefront_order", "=", True),
            ("company_id", "=", self.env.company.id),
        ]

    @api.model
    def _almohit_find_cart(self, partner):
        return self.search(
            self._almohit_cart_domain(partner),
            order="id desc",
            limit=1,
        )

    @api.model
    def _almohit_get_or_create_cart(self, partner):
        cart = self._almohit_find_cart(partner)
        pricelist = partner.property_product_pricelist
        if cart:
            if pricelist and cart.pricelist_id != pricelist:
                cart.pricelist_id = pricelist.id
            return cart
        values = {
            "partner_id": partner.id,
            "almohit_storefront_order": True,
            "company_id": self.env.company.id,
        }
        if pricelist:
            values["pricelist_id"] = pricelist.id
        return self.create(values)

    def action_confirm(self):
        result = super().action_confirm()

        confirmed = self.filtered(
            lambda order:
                order.almohit_storefront_order
                and order.state in ("sale", "done")
        )

        for order in confirmed:
            schedule_notification(EVENT_ORDER_CONFIRMED, order)

        return result
