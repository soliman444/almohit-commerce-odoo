from odoo import _, fields, models

from ..services.warehouse_common import ICP_RECEIPT_MODE
from ..services.whatsapp import (
    ICP_ACCOUNT_PHONE,
    ICP_ENABLED,
    ICP_SERVICE_TOKEN,
    ICP_SERVICE_URL,
    whatsapp_config,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    almohit_receipt_validation_mode = fields.Selection(
        selection=[
            ("both", "Buyer and Warehouse Manager"),
            ("warehouse_only", "Warehouse Manager only"),
            ("buyer_only", "Buyer only"),
        ],
        string="Purchase Receipt Validation",
        help=(
            "Controls who may validate purchase incoming receipts. "
            "Default both keeps /almohit/api/buyer/receipts working while "
            "warehouse managers use /almohit/api/warehouse/receipts."
        ),
        default="both",
        config_parameter=ICP_RECEIPT_MODE,
    )
    almohit_storefront_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Almohit Storefront Sales Warehouse",
        domain="[('company_id', '=', company_id)]",
        help="Warehouse used for storefront stock, carts and sales.",
    )
    almohit_whatsapp_enabled = fields.Boolean(
        string="Enable WhatsApp Notifications",
        help="When disabled, business operations still succeed and WhatsApp is skipped.",
    )
    almohit_whatsapp_account_phone = fields.Char(
        string="WhatsApp Account Number",
        help="Business WhatsApp number used to send Almohit notifications, for example +9665xxxxxxxx.",
    )
    almohit_whatsapp_service_url = fields.Char(
        string="WhatsApp Service URL",
        help="HTTP URL of the Almohit WhatsApp send service.",
    )
    almohit_whatsapp_service_token = fields.Char(
        string="Service Token",
        help="Shared Bearer token between Odoo and the WhatsApp send service.",
    )

    def get_values(self):
        res = super().get_values()
        cfg = whatsapp_config(self.env)
        res.update(
            almohit_whatsapp_enabled=cfg["enabled"],
            almohit_whatsapp_account_phone=cfg["account_phone"],
            almohit_whatsapp_service_url=cfg["url"],
            almohit_whatsapp_service_token=cfg["token"],
        )
        warehouse = self.env["stock.warehouse"].sudo().search([
            ("id", "=", int(
                self.env["ir.config_parameter"].sudo().get_param(
                    "almohit.storefront_warehouse_id", "0"
                ) or "0"
            )),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        res["almohit_storefront_warehouse_id"] = warehouse.id or False
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(ICP_ENABLED, "True" if self.almohit_whatsapp_enabled else "False")
        icp.set_param(ICP_ACCOUNT_PHONE, (self.almohit_whatsapp_account_phone or "").strip())
        icp.set_param(
            ICP_SERVICE_URL,
            (self.almohit_whatsapp_service_url or "").strip().rstrip("/"),
        )
        icp.set_param(ICP_SERVICE_TOKEN, (self.almohit_whatsapp_service_token or "").strip())
        icp.set_param(
            "almohit.storefront_warehouse_id",
            self.almohit_storefront_warehouse_id.id or "",
        )

    def action_open_whatsapp_account(self):
        return {
            "type": "ir.actions.client",
            "tag": "almohit_whatsapp_account",
            "name": _("WhatsApp Account"),
            "target": "current",
        }
