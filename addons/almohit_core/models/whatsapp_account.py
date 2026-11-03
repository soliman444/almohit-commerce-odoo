from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from ..services.whatsapp import (
    ICP_ACCOUNT_PHONE,
    normalize_account_phone,
    phones_match,
    whatsapp_config,
    whatsapp_session_info,
    whatsapp_unlink_session,
)


class AlmohitWhatsAppAccount(models.TransientModel):
    _name = "almohit.whatsapp.account"
    _description = "WhatsApp Account"

    def _require_whatsapp_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can manage the WhatsApp account."))

    def _phase_payload(self, info, cfg):
        info = info or {}
        desired = (cfg.get("account_phone") or "").strip()
        phase = info.get("phase") or "unknown"
        connected = bool(info.get("connected")) and phase == "connected"
        connected_phone = (info.get("phone") or "").strip() if connected else ""
        service_reachable = bool(info.get("service_reachable", info.get("ok")))
        if phase == "service_unreachable" or not service_reachable:
            status_text = info.get("status_text") or _("Could not verify")
        elif info.get("verification_failed"):
            status_text = _("Could not verify")
        else:
            status_text = info.get("status_text") or ""
        match = bool(
            connected and connected_phone and desired and phones_match(connected_phone, desired)
        )
        mismatch = bool(
            connected and connected_phone and desired and not phones_match(connected_phone, desired)
        )
        if mismatch:
            status_text = _("Connected number does not match the desired number")
        return {
            "connected": connected,
            "phase": phase,
            "status_text": status_text,
            "connected_phone": connected_phone,
            "desired_phone": desired,
            "phones_match": match,
            "phone_mismatch": mismatch,
            "show_change_account": bool(desired) and (not connected or not match),
            "qr": False if connected else (info.get("qr") or False),
            "verification_failed": bool(info.get("verification_failed")),
            "last_verified_at": info.get("last_verified_at") or False,
            "service_reachable": service_reachable,
            "can_manage": self.env.user.has_group("base.group_system"),
        }

    @api.model
    def get_live_state(self):
        self._require_whatsapp_admin()
        cfg = whatsapp_config(self.env)
        info = whatsapp_session_info(self.env)
        return self._phase_payload(info, cfg)

    @api.model
    def change_account(self, phone=None):
        """Confirm change: save desired phone, unlink once, wait for new QR."""
        self._require_whatsapp_admin()
        cfg = whatsapp_config(self.env)
        desired_raw = phone if phone is not None else cfg.get("account_phone")
        normalized = normalize_account_phone(desired_raw)
        if not normalized:
            raise UserError(
                _("Enter the WhatsApp number in international format, for example +9665xxxxxxxx.")
            )
        self.env["ir.config_parameter"].sudo().set_param(ICP_ACCOUNT_PHONE, normalized)
        info, error = whatsapp_unlink_session(self.env)
        if error:
            return {"ok": False, "error": error, **self.get_live_state()}
        return {
            "ok": True,
            "error": False,
            **self._phase_payload(info or whatsapp_session_info(self.env), whatsapp_config(self.env)),
        }
