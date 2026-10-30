from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class AlmohitWarehouseAssignment(models.Model):
    _name = "almohit.warehouse.assignment"
    _description = "Almohit Warehouse Manager Assignment"
    _order = "id desc"
    _rec_name = "partner_id"

    partner_id = fields.Many2one(
        "res.partner",
        string="Warehouse Manager",
        required=True,
        index=True,
        ondelete="cascade",
        domain="[('almohit_app_role', '=', 'warehouse_manager')]",
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        compute="_compute_user_id",
        store=True,
        index=True,
        readonly=True,
    )
    warehouse_ids = fields.Many2many(
        "stock.warehouse",
        "almohit_warehouse_assignment_rel",
        "assignment_id",
        "warehouse_id",
        string="Allowed Warehouses",
        required=True,
    )
    default_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Default Warehouse",
        help="Must be one of the allowed warehouses.",
    )
    perm_stock_read = fields.Boolean(string="Read Stock", default=True)
    perm_receipt = fields.Boolean(string="Receive Goods", default=False)
    perm_issue = fields.Boolean(string="Issue Goods", default=False)
    perm_transfer_create = fields.Boolean(string="Create Transfers", default=False)
    perm_transfer_receive = fields.Boolean(string="Receive Transfers", default=False)
    active = fields.Boolean(default=True)
    notes = fields.Text()

    _sql_constraints = [
        (
            "partner_uniq",
            "unique(partner_id)",
            "Each warehouse manager can have only one assignment record.",
        ),
    ]

    @api.depends("partner_id")
    def _compute_user_id(self):
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        for rec in self:
            user = Users.search(
                [("partner_id", "=", rec.partner_id.id)],
                limit=1,
            ) if rec.partner_id else Users.browse()
            rec.user_id = user.id if user else False

    @api.constrains("warehouse_ids", "default_warehouse_id")
    def _check_default_warehouse(self):
        for rec in self:
            if not rec.warehouse_ids:
                raise ValidationError(_("Assign at least one warehouse."))
            if (
                rec.default_warehouse_id
                and rec.default_warehouse_id not in rec.warehouse_ids
            ):
                raise ValidationError(
                    _("Default warehouse must be one of the allowed warehouses.")
                )

    @api.onchange("warehouse_ids")
    def _onchange_warehouse_ids(self):
        if self.default_warehouse_id and self.default_warehouse_id not in self.warehouse_ids:
            self.default_warehouse_id = False
        if not self.default_warehouse_id and self.warehouse_ids:
            self.default_warehouse_id = self.warehouse_ids[:1]

    def permission_codes(self):
        self.ensure_one()
        codes = []
        if self.perm_stock_read:
            codes.append("stock_read")
        if self.perm_receipt:
            codes.append("receipt")
        if self.perm_issue:
            codes.append("issue")
        if self.perm_transfer_create:
            codes.append("transfer_create")
        if self.perm_transfer_receive:
            codes.append("transfer_receive")
        return codes
