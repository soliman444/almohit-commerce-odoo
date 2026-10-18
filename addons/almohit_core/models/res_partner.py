from odoo import fields, models

ALMOHIT_ROLE_GROUP_XMLIDS = {
    "buyer": "almohit_core.group_almohit_buyer",
    "merchant": "almohit_core.group_almohit_merchant",
    "marketer": "almohit_core.group_almohit_marketer",
    "distributor": "almohit_core.group_almohit_distributor",
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    almohit_trade_status = fields.Selection(
        selection=[
            ("normal", "Normal"),
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Almohit Trade Status",
        default="normal",
        required=True,
        index=True,
    )
    almohit_app_role = fields.Selection(
        selection=[
            ("merchant", "Merchant"),
            ("buyer", "Buyer"),
            ("distributor", "Distributor"),
            ("marketer", "Marketer"),
        ],
        string="Almohit App Role",
        default="merchant",
        required=True,
        index=True,
    )
    almohit_manager_name = fields.Char(string="Shop Manager")
    almohit_employee_name = fields.Char(string="Other Employee")
    almohit_bank_account = fields.Char(string="Bank Account")
    almohit_payment_method = fields.Selection(
        selection=[
            ("bank_transfer", "Bank transfer"),
            ("cash", "Cash"),
            ("both", "Bank transfer or cash"),
        ],
        string="Preferred Payment Method",
    )
    almohit_shop_image = fields.Image(string="Shop Photo", max_width=1920, max_height=1920)
    almohit_recruited_by_id = fields.Many2one(
        "res.partner",
        string="Recruited By",
        index=True,
        ondelete="set null",
    )

    def write(self, vals):
        res = super().write(vals)
        if "almohit_app_role" in vals:
            self._sync_almohit_role_groups()
        return res

    def _almohit_role_groups(self):
        groups = {}
        for role, xmlid in ALMOHIT_ROLE_GROUP_XMLIDS.items():
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups[role] = group
        return groups

    def _sync_almohit_role_groups(self):
        role_groups = self._almohit_role_groups()
        all_group_ids = {group.id for group in role_groups.values()}
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        for partner in self:
            users = Users.search([("partner_id", "=", partner.id)])
            if not users:
                continue
            target = role_groups.get(partner.almohit_app_role)
            commands = [
                (3, group_id)
                for group_id in all_group_ids
                if not target or group_id != target.id
            ]
            if target:
                commands.append((4, target.id))
            if commands:
                users.write({"groups_id": commands})
