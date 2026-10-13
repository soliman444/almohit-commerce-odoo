import logging

from odoo import api, fields, models

from ..services.whatsapp import CHANNEL, post_send

_logger = logging.getLogger(__name__)


class AlmohitNotificationLog(models.Model):
    _name = "almohit.notification.log"
    _description = "Almohit Notification Log"
    _order = "id desc"

    event_code = fields.Char(required=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    channel = fields.Selection(
        selection=[("whatsapp", "WhatsApp")],
        default=CHANNEL,
        required=True,
        index=True,
    )
    partner_id = fields.Many2one("res.partner", ondelete="set null", index=True)
    recipient = fields.Char()
    recipient_masked = fields.Char()
    message = fields.Text(string="Notification Text")
    template_id = fields.Many2one(
        "almohit.notification.template",
        string="Template",
        ondelete="set null",
        index=True,
    )
    render_source = fields.Selection(
        selection=[
            ("template", "Template"),
            ("fallback", "Fallback"),
            ("disabled", "Disabled"),
        ],
        string="Render Source",
    )
    status = fields.Selection(
        selection=[
            ("queued", "Queued"),
            ("submitted", "Submitted"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    message_id = fields.Char(string="Provider Message Id")
    error = fields.Char()
    attempt_count = fields.Integer(default=0)
    sent_at = fields.Datetime()

    _sql_constraints = [
        (
            "event_channel_uniq",
            "unique(event_code, res_model, res_id, channel)",
            "This notification event was already logged.",
        ),
    ]

    def _almohit_deliver(self):
        for log in self:
            try:
                if log.status in ("submitted", "sent", "delivered", "read", "skipped"):
                    continue
                ok, error_code, message_id = post_send(
                    log.recipient, log.message, env=log.env
                )
                values = {"attempt_count": (log.attempt_count or 0) + 1}
                if ok:
                    values.update({
                        "status": "submitted",
                        "message_id": message_id or False,
                        "error": False,
                        "sent_at": False,
                    })
                else:
                    values.update({
                        "status": "failed",
                        "error": error_code or "SEND_FAILED",
                    })
                log.write(values)
                _logger.info(
                    "whatsapp deliver event=%s model=%s res_id=%s masked=%s status=%s error=%s",
                    log.event_code,
                    log.res_model,
                    log.res_id,
                    log.recipient_masked,
                    values["status"],
                    values.get("error") or "",
                )
            except Exception:
                _logger.exception(
                    "whatsapp deliver exception event=%s model=%s res_id=%s",
                    log.event_code,
                    log.res_model,
                    log.res_id,
                )

    @api.model
    def _cron_retry_failed(self):
        from datetime import timedelta

        failed = self.search(
            [
                ("channel", "=", CHANNEL),
                ("status", "=", "failed"),
                ("attempt_count", "<", 3),
            ],
            limit=40,
            order="id asc",
        )
        stale = fields.Datetime.now() - timedelta(minutes=5)
        queued = self.search(
            [
                ("channel", "=", CHANNEL),
                ("status", "=", "queued"),
                ("attempt_count", "=", 0),
                ("write_date", "<", stale),
            ],
            limit=10,
            order="id asc",
        )
        (failed | queued)._almohit_deliver()
