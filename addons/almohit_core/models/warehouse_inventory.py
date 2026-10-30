from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class AlmohitInventorySession(models.Model):
    _name = "almohit.inventory.session"
    _description = "Warehouse Inventory / Opening Balance Session"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(required=True, copy=False, default="New")
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("preview", "Preview"),
            ("approved", "Approved"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        index=True,
        copy=False,
    )
    session_type = fields.Selection(
        selection=[
            ("count", "Inventory Count"),
            ("opening", "Opening Balance"),
        ],
        required=True,
        default="count",
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    warehouse_id = fields.Many2one("stock.warehouse", required=True, index=True)
    location_id = fields.Many2one("stock.location", required=True)
    created_by_id = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
        readonly=True,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)
    import_checksum = fields.Char(index=True, copy=False)
    source_filename = fields.Char(copy=False)
    note = fields.Text()
    line_ids = fields.One2many(
        "almohit.inventory.session.line",
        "session_id",
        string="Lines",
        copy=True,
    )

    _sql_constraints = [
        (
            "import_checksum_uniq",
            "unique(company_id, import_checksum)",
            "This import file was already processed.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "almohit.inventory.session"
                ) or _("WH/INV/%s") % fields.Datetime.now()
        return super().create(vals_list)

    def action_cancel(self):
        for rec in self:
            if rec.state == "approved":
                raise UserError(_("Approved sessions cannot be cancelled."))
            rec.state = "cancelled"
        return True


class AlmohitInventorySessionLine(models.Model):
    _name = "almohit.inventory.session.line"
    _description = "Warehouse Inventory Session Line"

    session_id = fields.Many2one(
        "almohit.inventory.session",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one("product.product", required=True)
    product_uom_id = fields.Many2one("uom.uom", required=True)
    qty_on_hand = fields.Float(string="System Qty", default=0.0)
    qty_counted = fields.Float(string="Counted Qty", required=True, default=0.0)
    qty_difference = fields.Float(compute="_compute_difference", store=True)
    note = fields.Char()
    row_number = fields.Integer()

    @api.depends("qty_on_hand", "qty_counted")
    def _compute_difference(self):
        for line in self:
            line.qty_difference = float(line.qty_counted or 0.0) - float(line.qty_on_hand or 0.0)

    @api.constrains("product_id", "session_id")
    def _check_unique_product(self):
        for line in self:
            duplicates = self.search_count([
                ("id", "!=", line.id),
                ("session_id", "=", line.session_id.id),
                ("product_id", "=", line.product_id.id),
            ])
            if duplicates:
                raise ValidationError(
                    _("Product %s is duplicated in this inventory session.")
                    % line.product_id.display_name
                )
