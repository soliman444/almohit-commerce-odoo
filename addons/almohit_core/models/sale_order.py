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
        from ..services.stock import company_warehouse

        cart = self._almohit_find_cart(partner)
        pricelist = partner.property_product_pricelist
        warehouse = company_warehouse(self.env)
        if cart:
            if warehouse and cart.warehouse_id != warehouse:
                cart.warehouse_id = warehouse.id
            return cart
        values = {
            "partner_id": partner.id,
            "almohit_storefront_order": True,
            "company_id": self.env.company.id,
            "warehouse_id": warehouse.id,
        }
        if pricelist:
            values["pricelist_id"] = pricelist.id
        return self.create(values)

    def _almohit_set_cart_pricelist(self, pricelist):
        self.ensure_one()
        if not self.almohit_storefront_order:
            raise ValueError("Not a storefront cart.")
        if self.state != "draft":
            raise ValueError("Currency cannot be changed after checkout.")
        if self.company_id != self.env.company:
            raise ValueError("Invalid cart company.")
        if self.pricelist_id.id != pricelist.id:
            self.write({"pricelist_id": pricelist.id})
            self._recompute_prices()
            self.invalidate_recordset()
        if self.currency_id.id != pricelist.currency_id.id:
            raise ValueError("Cart currency does not match its pricelist.")
        return self

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
