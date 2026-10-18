from odoo import fields, models


class AlmohitWhatsAppSessionLog(models.Model):
    _name = "almohit.whatsapp.session.log"
    _description = "WhatsApp Session Log"
    _order = "id desc"

    event_type = fields.Selection(
        selection=[
            ("qr_ready", "QR Ready"),
            ("connected", "Connected"),
            ("disconnected", "Disconnected"),
            ("session_lost", "Session Lost"),
            ("unlink_requested", "Unlink Requested"),
            ("unlinked", "Unlinked"),
            ("service_error", "Service Error"),
            ("session_error", "Session Error"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    status = fields.Char(readonly=True)
    message = fields.Text(readonly=True)
    connected = fields.Boolean(readonly=True)
    phone = fields.Char(readonly=True)
