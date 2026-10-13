from odoo import fields, models


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