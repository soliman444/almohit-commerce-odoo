from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class AlmohitWarehouseTransfer(models.Model):
    _name = "almohit.warehouse.transfer"
    _description = "Inter-Warehouse Transfer"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(required=True, copy=False, default="New", readonly=True)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("dispatched", "Dispatched"),
            ("partially_received", "Partially Received"),
            ("received", "Received"),
            ("closed", "Closed"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
            ("discrepancy", "Discrepancy"),
        ],
        default="draft",
        required=True,
        index=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    source_warehouse_id = fields.Many2one("stock.warehouse", required=True, index=True)
    dest_warehouse_id = fields.Many2one("stock.warehouse", required=True, index=True)
    requested_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    dispatched_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    received_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    requested_at = fields.Datetime(readonly=True, copy=False)
    dispatched_at = fields.Datetime(readonly=True, copy=False)
    received_at = fields.Datetime(readonly=True, copy=False)
    note = fields.Text()
    line_ids = fields.One2many(
        "almohit.warehouse.transfer.line",
        "transfer_id",
        string="Lines",
        copy=True,
    )
    dispatch_picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    receive_picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    discrepancy_ids = fields.One2many(
        "almohit.warehouse.discrepancy",
        "transfer_id",
        string="Discrepancies",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "almohit.warehouse.transfer"
                ) or _("WH/TR/%s") % fields.Datetime.now()
        return super().create(vals_list)

    @api.constrains("source_warehouse_id", "dest_warehouse_id")
    def _check_warehouses(self):
        for rec in self:
            if rec.source_warehouse_id == rec.dest_warehouse_id:
                raise ValidationError(_("Source and destination warehouses must differ."))

    def action_cancel(self):
        for rec in self:
            if rec.state in ("received", "closed"):
                raise UserError(_("Cannot cancel a completed transfer."))
            rec.state = "cancelled"
        return True


class AlmohitWarehouseTransferLine(models.Model):
    _name = "almohit.warehouse.transfer.line"
    _description = "Inter-Warehouse Transfer Line"

    transfer_id = fields.Many2one(
        "almohit.warehouse.transfer",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one("product.product", required=True)
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        required=True,
    )
    qty_requested = fields.Float(required=True, default=0.0)
    qty_dispatched = fields.Float(default=0.0, copy=False)
    qty_received = fields.Float(default=0.0, copy=False)
    source_location_id = fields.Many2one("stock.location")
    dest_location_id = fields.Many2one("stock.location")
    dispatch_move_id = fields.Many2one("stock.move", copy=False)
    receive_move_id = fields.Many2one("stock.move", copy=False)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id


class AlmohitWarehouseDiscrepancy(models.Model):
    _name = "almohit.warehouse.discrepancy"
    _description = "Warehouse Transfer Discrepancy"
    _order = "id desc"

    transfer_id = fields.Many2one(
        "almohit.warehouse.transfer",
        required=True,
        ondelete="cascade",
        index=True,
    )
    line_id = fields.Many2one("almohit.warehouse.transfer.line", ondelete="set null")
    product_id = fields.Many2one("product.product", required=True)
    qty_expected = fields.Float(required=True)
    qty_received = fields.Float(required=True)
    qty_difference = fields.Float(required=True)
    reason = fields.Selection(
        selection=[
            ("missing", "Missing"),
            ("excess", "Excess"),
            ("damaged", "Damaged"),
            ("rejected", "Rejected"),
            ("other", "Other"),
        ],
        default="missing",
        required=True,
    )
    state = fields.Selection(
        selection=[
            ("open", "Open"),
            ("reconciled", "Reconciled"),
        ],
        default="open",
        required=True,
        index=True,
    )
    note = fields.Text()
    resolved_by_id = fields.Many2one("res.users", readonly=True)
    resolved_at = fields.Datetime(readonly=True)
