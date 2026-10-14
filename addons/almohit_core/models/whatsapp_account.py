from odoo import api, models

from ..services.whatsapp import (
    ICP_ACCOUNT_PHONE,
    whatsapp_config,
    whatsapp_session_info,
    whatsapp_unlink_session,
)


class AlmohitWhatsAppAccount(models.TransientModel):
    _name = "almohit.whatsapp.account"
    _description = "WhatsApp Account"

    @api.model
    def get_live_state(self):
        cfg = whatsapp_config(self.env)
        info = whatsapp_session_info(self.env)
        connected = bool(info.get("connected"))
        phone = (info.get("phone") or "").strip()
        if connected and phone and phone != (cfg.get("account_phone") or "").strip():
            self.env["ir.config_parameter"].sudo().set_param(ICP_ACCOUNT_PHONE, phone)
            cfg = whatsapp_config(self.env)
        return {
            "connected": connected,
            "status_text": info.get("status_text") or "",
            "phone": phone if connected else "",
            "qr": False if connected else (info.get("qr") or False),
            "account_phone": cfg.get("account_phone") or "",
            "enabled": bool(cfg.get("enabled")),
        }

    @api.model
    def unlink_account(self):
        info, error = whatsapp_unlink_session(self.env)
        if error:
            return {"ok": False, "error": error, **self.get_live_state()}
        if info:
            phone = (info.get("phone") or "").strip()
            if info.get("connected") and phone:
                self.env["ir.config_parameter"].sudo().set_param(ICP_ACCOUNT_PHONE, phone)
        return {"ok": True, "error": False, **self.get_live_state()}
