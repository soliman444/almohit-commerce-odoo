from odoo import api, models
from odoo.exceptions import UserError

from ..services.payments import order_allows_distribution
from ..services.stock import picking_allows_distribution
from ..services.whatsapp import (
    EVENT_DELIVERY_DONE,
    EVENT_DISTRIBUTION_ASSIGNED,
    schedule_notification,
)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _almohit_requires_payment_to_assign(self):
        self.ensure_one()
        if self.picking_type_code != "outgoing" and self.picking_type_id.code != "outgoing":
            return False
        sale = self.sale_id
        if not sale and self.origin:
            sale = self.env["sale.order"].sudo().search(
                [("name", "=", self.origin), ("company_id", "=", self.company_id.id)],
                limit=1,
            )
        return bool(sale and sale.almohit_storefront_order)

    def _almohit_assert_payment_before_assign(self):
        if self.env.context.get("skip_almohit_payment_assign"):
            return
        for picking in self:
            if not picking.user_id or not picking._almohit_requires_payment_to_assign():
                continue
            if not picking_allows_distribution(picking):
                raise UserError(
                    "لا يمكن إسناد الطلب للتوزيع قبل سداد المبلغ المستحق حاليًا حسب شروط الدفع."
                )

    def _almohit_notify_distribution_assigned(self, previous_user_ids):
        if self.env.context.get("skip_almohit_payment_assign"):
            return
        for picking in self:
            if not picking._almohit_requires_payment_to_assign():
                continue
            new_uid = picking.user_id.id
            old_uid = previous_user_ids.get(picking.id) or False
            if not new_uid or new_uid == old_uid:
                continue
            order = picking._almohit_storefront_sale()
            if order:
                schedule_notification(
                    EVENT_DISTRIBUTION_ASSIGNED, picking, extra={"order": order}
                )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        assigned = records.filtered("user_id")
        assigned._almohit_assert_payment_before_assign()
        assigned._almohit_notify_distribution_assigned(
            {picking.id: False for picking in assigned}
        )
        return records

    def write(self, vals):
        previous = {}
        if "user_id" in vals:
            previous = {picking.id: picking.user_id.id for picking in self}
        res = super().write(vals)
        if "user_id" in vals:
            changed = self.filtered(
                lambda picking: picking.user_id.id
                and picking.user_id.id != (previous.get(picking.id) or False)
            )
            changed._almohit_assert_payment_before_assign()
            changed._almohit_notify_distribution_assigned(previous)
        return res

    def _almohit_storefront_sale(self):
        self.ensure_one()
        sale = self.sale_id
        if not sale and self.origin:
            sale = self.env["sale.order"].sudo().search(
                [("name", "=", self.origin), ("company_id", "=", self.company_id.id)],
                limit=1,
            )
        if sale and sale.almohit_storefront_order:
            return sale
        return self.env["sale.order"]

    def _almohit_assert_payment_before_validate(self):
        for picking in self:
            if picking.picking_type_code != "outgoing" and picking.picking_type_id.code != "outgoing":
                continue
            order = picking._almohit_storefront_sale()
            if not order:
                continue
            if not order_allows_distribution(order):
                raise UserError(
                    "لا يمكن تأكيد التسليم قبل سداد المبلغ المستحق حاليًا حسب شروط الدفع."
                )

    def button_validate(self):
        self._almohit_assert_payment_before_validate()
        return super().button_validate()

    def _action_done(self):
        self._almohit_assert_payment_before_validate()
        result = super()._action_done()
        done = self.filtered(
            lambda picking: picking.state == "done" and picking.picking_type_id.code == "outgoing"
        )
        for picking in done:
            order = picking._almohit_storefront_sale()
            if order:
                schedule_notification(EVENT_DELIVERY_DONE, picking, extra={"order": order})
        return result
