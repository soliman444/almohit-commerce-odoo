from odoo import _, fields, models

from ..services.whatsapp import (
    ICP_ACCOUNT_PHONE,
    ICP_ENABLED,
    ICP_SERVICE_TOKEN,
    ICP_SERVICE_URL,
    whatsapp_config,
    whatsapp_service_status,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    almohit_whatsapp_enabled = fields.Boolean(
        string="Enable WhatsApp Notifications",
        help="When disabled, business operations still succeed and WhatsApp is skipped.",
    )
    almohit_whatsapp_account_phone = fields.Char(
        string="WhatsApp Account Number",
        help="Business WhatsApp number used to send Almohit notifications, for example +9665xxxxxxxx. Link the account by scanning the QR code in the WhatsApp service logs.",
    )
    almohit_whatsapp_service_url = fields.Char(
        string="WhatsApp Service URL",
        help="HTTP URL of the Almohit WhatsApp send service.",
    )
    almohit_whatsapp_service_token = fields.Char(
        string="Service Token",
        help="Shared Bearer token between Odoo and the WhatsApp send service.",
    )
    almohit_whatsapp_connection = fields.Char(
        string="Connection Status",
        readonly=True,
    )

    def get_values(self):
        res = super().get_values()
        cfg = whatsapp_config(self.env)
        res.update(
            almohit_whatsapp_enabled=cfg["enabled"],
            almohit_whatsapp_account_phone=cfg["account_phone"],
            almohit_whatsapp_service_url=cfg["url"],
            almohit_whatsapp_service_token=cfg["token"],
            almohit_whatsapp_connection=whatsapp_service_status(cfg["url"]),
        )
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

    def action_almohit_whatsapp_check(self):
        self.ensure_one()
        status = whatsapp_service_status(self.almohit_whatsapp_service_url)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("WhatsApp"),
                "message": status,
                "type": "success" if status.startswith("Connected") else "warning",
                "sticky": False,
            },
        }
