from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from ..services.whatsapp import EVENT_TRADE_APPROVED, schedule_notification


class AlmohitTradeApplication(models.Model):
    _name = "almohit.trade.application"
    _description = "Almohit Trade Application"
    _order = "id desc"
    _rec_name = "company_name"

    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        index=True,
        ondelete="cascade",
    )
    requested_role = fields.Selection(
        selection=[
            ("merchant", "Merchant"),
            ("buyer", "Buyer"),
            ("distributor", "Distributor"),
            ("marketer", "Marketer"),
        ],
        string="Requested Role",
        default="merchant",
        required=True,
        index=True,
    )
    company_name = fields.Char(required=True)
    tax_number = fields.Char(string="Commercial Registration")
    business_type = fields.Char()
    phone = fields.Char()
    street = fields.Char()
    city = fields.Char()
    manager_name = fields.Char(string="Shop Manager")
    employee_name = fields.Char(string="Other Employee")
    bank_account = fields.Char()
    payment_method = fields.Selection(
        selection=[
            ("bank_transfer", "Bank transfer"),
            ("cash", "Cash"),
            ("both", "Bank transfer or cash"),
        ],
        string="Preferred Payment Method",
    )
    shop_image = fields.Image(string="Shop Photo", max_width=1920, max_height=1920)
    notes = fields.Text()
    status = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="pending",
        required=True,
        index=True,
        readonly=True,
        copy=False,
    )
    reviewed_by = fields.Many2one(
        "res.users",
        string="Reviewed By",
        readonly=True,
        copy=False,
    )
    reviewed_at = fields.Datetime(
        string="Reviewed At",
        readonly=True,
        copy=False,
    )
    rejection_reason = fields.Text(copy=False)
    recruited_by_id = fields.Many2one(
        "res.partner",
        string="Recruited By",
        index=True,
        ondelete="set null",
        copy=False,
    )

    @api.constrains("partner_id", "status")
    def _check_unique_pending(self):
        for rec in self:
            if rec.status != "pending" or not rec.partner_id:
                continue
            # Portal users have no model ACL. Uniqueness still has to run
            # after API create() via scoped sudo, filtered to this partner.
            duplicates = rec.sudo().search_count([
                ("id", "!=", rec.id),
                ("partner_id", "=", rec.partner_id.id),
                ("status", "=", "pending"),
            ])
            if duplicates:
                raise ValidationError(
                    _("This customer already has a pending trade application.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        pending = records.filtered(lambda rec: rec.status == "pending")
        pending.mapped("partner_id").sudo().write({
            "almohit_trade_status": "pending",
        })
        return records

    def action_approve(self):
        for rec in self:
            if rec.status != "pending":
                raise UserError(
                    _("Only pending trade applications can be approved.")
                )
            rec.write({
                "status": "approved",
                "reviewed_by": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
                "rejection_reason": False,
            })
            rec.partner_id.write(rec._partner_approval_values())
            schedule_notification(EVENT_TRADE_APPROVED, rec)
        return True

    def action_reject(self):
        for rec in self:
            if rec.status != "pending":
                raise UserError(
                    _("Only pending trade applications can be rejected.")
                )
            rec.write({
                "status": "rejected",
                "reviewed_by": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
            })
            rec.partner_id.write({
                "almohit_trade_status": "rejected",
                "property_product_pricelist": self._fallback_pricelist().id,
            })
        return True

    def _partner_approval_values(self):
        self.ensure_one()
        values = {
            "almohit_trade_status": "approved",
            "almohit_app_role": self.requested_role or "merchant",
            "property_product_pricelist": self._wholesale_pricelist().id,
        }
        if self.tax_number:
            values["vat"] = self.tax_number
        if self.street:
            values["street"] = self.street
        if self.city:
            values["city"] = self.city
        if self.phone:
            values["phone"] = self.phone
        if self.manager_name:
            values["almohit_manager_name"] = self.manager_name
        if self.employee_name:
            values["almohit_employee_name"] = self.employee_name
        if self.bank_account:
            values["almohit_bank_account"] = self.bank_account
        if self.payment_method:
            values["almohit_payment_method"] = self.payment_method
        if self.shop_image:
            values["almohit_shop_image"] = self.shop_image
        if self.recruited_by_id:
            values["almohit_recruited_by_id"] = self.recruited_by_id.id
        return values

    def _wholesale_pricelist(self):
        return self.env.ref("almohit_core.pricelist_wholesale")

    def _fallback_pricelist(self):
        wholesale = self.env.ref(
            "almohit_core.pricelist_wholesale",
            raise_if_not_found=False,
        )
        domain = [
            ("active", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ]
        if wholesale:
            domain.append(("id", "!=", wholesale.id))
        fallback = self.env["product.pricelist"].search(
            domain,
            order="sequence, id",
            limit=1,
        )
        if fallback:
            return fallback
        return self.env["product.pricelist"].sudo().create(
            self.env.company._get_default_pricelist_vals()
        )
