"""Outbound WhatsApp notifications. Never required for business success.

Pipeline for every event:
  model hook detects the business event
  → schedule_notification(event_code, record, extra)
  → resolve recipient
  → render_message (whatsapp_messages)
  → normalize phone
  → claim notification log
  → postcommit / new cursor
  → HTTP send
  → sent | failed | skipped
  → cron retries failed/queued

Phone: res.partner.mobile, else res.partner.phone. No hard-coded numbers.
HTTP is never inside the business transaction.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request

import odoo
from odoo import SUPERUSER_ID, api
from psycopg2 import IntegrityError

from . import whatsapp_messages
from .payments import order_allows_distribution

_logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 4
CHANNEL = "whatsapp"

EVENT_TRADE_APPROVED = "trade_approved"
EVENT_ORDER_CONFIRMED = "order_confirmed"
EVENT_INVOICE_POSTED = "invoice_posted"
EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED = "distribution_payment_requirement_satisfied"
EVENT_PAYMENT_RECEIVED = "payment_received"
EVENT_INVOICE_PAID = "invoice_paid"
EVENT_DISTRIBUTION_ASSIGNED = "distribution_assigned"
EVENT_DELIVERY_DONE = "delivery_done"

FINAL_EVENTS = (
    EVENT_TRADE_APPROVED,
    EVENT_ORDER_CONFIRMED,
    EVENT_INVOICE_POSTED,
    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
    EVENT_PAYMENT_RECEIVED,
    EVENT_INVOICE_PAID,
    EVENT_DISTRIBUTION_ASSIGNED,
    EVENT_DELIVERY_DONE,
)


ICP_ENABLED = "almohit.whatsapp.enabled"
ICP_ACCOUNT_PHONE = "almohit.whatsapp.account_phone"
ICP_SERVICE_URL = "almohit.whatsapp.service_url"
ICP_SERVICE_TOKEN = "almohit.whatsapp.service_token"


def _env_flag(name):
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes")


def _truthy(raw):
    return str(raw or "").strip().lower() in ("1", "true", "yes")


def whatsapp_config(env=None):
    """UI ir.config_parameter wins when set; Docker env remains the fallback."""
    enabled = _env_flag("WHATSAPP_ENABLED")
    url = (os.getenv("WHATSAPP_SERVICE_URL") or "").strip().rstrip("/")
    token = (os.getenv("WHATSAPP_SERVICE_TOKEN") or "").strip()
    account_phone = ""
    if env is not None:
        icp = env["ir.config_parameter"].sudo()
        enabled_param = icp.get_param(ICP_ENABLED)
        if enabled_param not in (None, False, ""):
            enabled = _truthy(enabled_param)
        url = (icp.get_param(ICP_SERVICE_URL) or "").strip().rstrip("/") or url
        token = (icp.get_param(ICP_SERVICE_TOKEN) or "").strip() or token
        account_phone = (icp.get_param(ICP_ACCOUNT_PHONE) or "").strip()
    return {
        "enabled": enabled,
        "url": url,
        "token": token,
        "account_phone": account_phone,
    }


def whatsapp_service_status(url, timeout=3):
    """Read-only health of the send service. Never required for business success."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return "Not configured"
    request = urllib.request.Request(base + "/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return "Service unreachable"
    if not isinstance(body, dict) or not body.get("ok"):
        return "Service unreachable"
    session = (body.get("whatsapp") or {}) if isinstance(body.get("whatsapp"), dict) else {}
    status = session.get("status") or "unknown"
    name = session.get("session") or ""
    if session.get("connected"):
        return "Connected%s" % ((" (%s)" % name) if name else "")
    return "Disconnected (%s)" % status


def mask_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 6:
        return "******"
    prefix = "+" if (phone or "").strip().startswith("+") else ""
    return "%s%s******%s" % (prefix, digits[:3], digits[-3:])


def normalize_phone(raw, country=None):
    """Conservative E.164. Skip rather than guess a country."""
    if not raw:
        return None
    compact = re.sub(r"[\s\-\(\)]", "", str(raw).strip())
    if not compact:
        return None
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    country_code = ""
    if country:
        country_code = str(country.phone_code or "").lstrip("+")
    if compact.startswith("+"):
        digits = re.sub(r"\D", "", compact)
        if 8 <= len(digits) <= 15:
            return "+" + digits
        return None
    digits = re.sub(r"\D", "", compact)
    if not digits:
        return None
    if digits.startswith("0") and country_code:
        national = digits.lstrip("0")
        if national:
            return "+%s%s" % (country_code, national)
        return None
    if country_code and digits.startswith(country_code) and 8 <= len(digits) <= 15:
        return "+" + digits
    if country_code and not digits.startswith(country_code) and 6 <= len(digits) <= 10:
        return "+%s%s" % (country_code, digits)
    if 11 <= len(digits) <= 15 and not digits.startswith("0"):
        return "+" + digits
    _logger.info(
        "whatsapp skip ambiguous phone masked=%s country=%s",
        mask_phone(raw),
        getattr(country, "code", None) or "",
    )
    return None


def partner_phone(partner):
    if not partner:
        return None, None
    country = partner.country_id
    raw = (partner.mobile or "").strip() or (partner.phone or "").strip()
    return raw, normalize_phone(raw, country)


def is_staff_partner(partner):
    if not partner:
        return False
    return bool(partner.user_ids.filtered(lambda user: not user.share))


def _order_from_extra(record, extra):
    extra = extra or {}
    order = extra.get("order")
    if order:
        return order
    if getattr(record, "_name", "") == "sale.order":
        return record
    return None


def resolve_recipient(event_code, record, extra=None):
    """Canonical recipient per event. No default distributor fallback."""
    extra = extra or {}
    if event_code == EVENT_TRADE_APPROVED:
        return record.partner_id
    if event_code == EVENT_DISTRIBUTION_ASSIGNED:
        user = record.user_id if record else None
        return user.partner_id if user else record.env["res.partner"]
    order = _order_from_extra(record, extra)
    if event_code in (
        EVENT_ORDER_CONFIRMED,
        EVENT_INVOICE_POSTED,
        EVENT_PAYMENT_RECEIVED,
        EVENT_INVOICE_PAID,
        EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
        EVENT_DELIVERY_DONE,
    ):
        return order.partner_id if order else None
    return None


def _distribution_assigned_event_code(env, picking, user):
    """Unique per actual assignment occurrence, not permanently per user."""
    previous = env["almohit.notification.log"].sudo().search_count(
        [
            ("res_model", "=", "stock.picking"),
            ("res_id", "=", picking.id),
            ("channel", "=", CHANNEL),
            ("event_code", "=like", EVENT_DISTRIBUTION_ASSIGNED + ":%"),
        ]
    )
    return "%s:%s:%s" % (EVENT_DISTRIBUTION_ASSIGNED, user.id, previous + 1)


def _payment_received_event_code(reconcile):
    """Unique per partial reconcile, not permanently per invoice."""
    return "%s:%s" % (EVENT_PAYMENT_RECEIVED, reconcile.id)


def notification_identity(event_code, record, extra=None):
    """(log_event_code, res_model, res_id) used by the unique constraint."""
    extra = extra or {}
    if event_code == EVENT_DISTRIBUTION_ASSIGNED:
        return (
            _distribution_assigned_event_code(record.env, record, record.user_id),
            record._name,
            record.id,
        )
    if event_code == EVENT_PAYMENT_RECEIVED:
        reconcile = extra.get("reconcile")
        if reconcile:
            return (
                _payment_received_event_code(reconcile),
                record._name,
                record.id,
            )
    return event_code, record._name, record.id


def _claim_log(env, event_code, res_model, res_id, partner, message, phone, extra_vals=None):
    Log = env["almohit.notification.log"].sudo()
    existing = Log.search(
        [
            ("event_code", "=", event_code),
            ("res_model", "=", res_model),
            ("res_id", "=", res_id),
            ("channel", "=", CHANNEL),
        ],
        limit=1,
    )
    if existing:
        return existing, False
    values = {
        "event_code": event_code,
        "res_model": res_model,
        "res_id": res_id,
        "channel": CHANNEL,
        "partner_id": partner.id if partner else False,
        "recipient": phone or "",
        "recipient_masked": mask_phone(phone) if phone else "",
        "message": message or "",
        "status": "queued",
        "attempt_count": 0,
    }
    if extra_vals:
        values.update(extra_vals)
    try:
        with env.cr.savepoint():
            return Log.create(values), True
    except IntegrityError:
        existing = Log.search(
            [
                ("event_code", "=", event_code),
                ("res_model", "=", res_model),
                ("res_id", "=", res_id),
                ("channel", "=", CHANNEL),
            ],
            limit=1,
        )
        return existing, False


def _register_postcommit(env, log_id):
    dbname = env.cr.dbname

    def _deliver():
        try:
            registry = odoo.registry(dbname)
            with registry.cursor() as new_cr:
                new_env = api.Environment(new_cr, SUPERUSER_ID, {})
                log = new_env["almohit.notification.log"].browse(log_id)
                if log.exists():
                    log._almohit_deliver()
                new_cr.commit()
        except Exception:
            _logger.exception(
                "whatsapp postcommit failed event_log=%s (business transaction already committed)",
                log_id,
            )

    env.cr.postcommit.add(_deliver)


def _enqueue(
    env,
    event_code,
    res_model,
    res_id,
    partner,
    message,
    allow_staff=False,
    extra_vals=None,
):
    """Claim a log in the current transaction; send only after commit. Never raises."""
    try:
        extra_vals = extra_vals or {}
        if not allow_staff and is_staff_partner(partner):
            _logger.info(
                "whatsapp skip staff partner event=%s model=%s res_id=%s",
                event_code,
                res_model,
                res_id,
            )
            return {"sent": False, "reason": "staff"}
        raw, phone = partner_phone(partner)
        cfg = whatsapp_config(env)
        if not phone:
            log, created = _claim_log(
                env, event_code, res_model, res_id, partner, message, raw, extra_vals=extra_vals
            )
            if created and log:
                log.write({
                    "status": "skipped",
                    "error": "NO_PHONE" if not raw else "AMBIGUOUS_PHONE",
                })
            _logger.info(
                "whatsapp skip event=%s model=%s res_id=%s reason=phone masked=%s",
                event_code,
                res_model,
                res_id,
                mask_phone(raw or ""),
            )
            return {"sent": False, "reason": "phone"}
        log, created = _claim_log(
            env, event_code, res_model, res_id, partner, message, phone, extra_vals=extra_vals
        )
        if not log:
            return {"sent": False, "reason": "claim_failed"}
        if log.status == "sent":
            return {"sent": False, "reason": "already_sent"}
        if log.status == "skipped":
            return {"sent": False, "reason": "skipped"}
        if not created and log.status in ("queued", "failed"):
            return {"sent": False, "reason": "already_claimed"}
        if not cfg["enabled"] or not cfg["url"] or not cfg["token"]:
            reason = "disabled" if not cfg["enabled"] else "unconfigured"
            log.write({"status": "skipped", "error": reason.upper()})
            _logger.info(
                "whatsapp %s event=%s model=%s res_id=%s masked=%s would_send=true",
                reason,
                event_code,
                res_model,
                res_id,
                mask_phone(phone),
            )
            return {"sent": False, "reason": reason}
        _register_postcommit(env, log.id)
        return {"sent": False, "reason": "queued"}
    except Exception:
        _logger.exception(
            "whatsapp schedule failed event=%s model=%s res_id=%s",
            event_code,
            res_model,
            res_id,
        )
        return {"sent": False, "reason": "error"}


def post_send(phone, message, env=None):
    cfg = whatsapp_config(env)
    if not cfg["enabled"]:
        return False, "WHATSAPP_DISABLED", None
    if not cfg["url"] or not cfg["token"]:
        return False, "WHATSAPP_UNCONFIGURED", None
    payload = json.dumps({"phone": phone, "message": message}).encode("utf-8")
    request = urllib.request.Request(
        cfg["url"] + "/send",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % cfg["token"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        code = ((body.get("error") or {}).get("code") if isinstance(body, dict) else None) or "HTTP_%s" % error.code
        return False, str(code), None
    except Exception as error:
        return False, type(error).__name__, None
    if not isinstance(body, dict) or not body.get("ok"):
        code = ((body.get("error") or {}).get("code") if isinstance(body, dict) else None) or "SEND_FAILED"
        return False, str(code), None
    return True, None, body.get("message_id") or None


def schedule_notification(event_code, record, extra=None):
    """Canonical entry: detect already happened; this renders, logs, and queues.

    Never raises onto the caller. Never writes accounting or stock.
    """
    extra = extra or {}
    try:
        if not record or event_code not in FINAL_EVENTS:
            return {"sent": False, "reason": "invalid"}
        if event_code == EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED:
            if not order_allows_distribution(record):
                return {"sent": False, "reason": "not_cleared"}
        allow_staff = False
        if event_code == EVENT_PAYMENT_RECEIVED:
            reconcile = extra.get("reconcile")
            if not reconcile or not reconcile.id:
                return {"sent": False, "reason": "invalid"}
        if event_code == EVENT_DISTRIBUTION_ASSIGNED:
            if not record.user_id:
                return {"sent": False, "reason": "no_user"}
            order = extra.get("order")
            if not order_allows_distribution(order):
                return {"sent": False, "reason": "not_cleared"}
            allow_staff = True
        partner = resolve_recipient(event_code, record, extra)
        log_code, res_model, res_id = notification_identity(event_code, record, extra)
        if event_code == EVENT_DISTRIBUTION_ASSIGNED and not partner:
            log, created = _claim_log(
                record.env, log_code, res_model, res_id, None, "", None
            )
            if created and log:
                log.write({"status": "skipped", "error": "NO_PARTNER"})
            return {"sent": False, "reason": "no_partner"}
        result = whatsapp_messages.render_message(event_code, record, extra)
        if not isinstance(result, whatsapp_messages.RenderResult):
            result = whatsapp_messages.RenderResult(
                text=result or "",
                source="fallback",
            )
        extra_vals = {}
        if result.template_id:
            extra_vals["template_id"] = result.template_id
        if result.source:
            extra_vals["render_source"] = result.source
        if result.skip:
            log, created = _claim_log(
                record.env,
                log_code,
                res_model,
                res_id,
                partner,
                "",
                None,
                extra_vals=extra_vals,
            )
            if created and log:
                log.write({
                    "status": "skipped",
                    "error": result.skip_reason or "TEMPLATE_DISABLED",
                    "message": "",
                })
            return {"sent": False, "reason": "template_disabled"}
        message = result.text
        if not message:
            log, created = _claim_log(
                record.env,
                log_code,
                res_model,
                res_id,
                partner,
                "",
                None,
                extra_vals=extra_vals,
            )
            if created and log:
                log.write({
                    "status": "failed",
                    "error": result.error or "NO_MESSAGE",
                })
            return {"sent": False, "reason": "no_message"}
        return _enqueue(
            record.env,
            log_code,
            res_model,
            res_id,
            partner,
            message,
            allow_staff=allow_staff,
            extra_vals=extra_vals,
        )
    except Exception:
        _logger.exception(
            "whatsapp schedule failed event=%s model=%s res_id=%s",
            event_code,
            getattr(record, "_name", None),
            getattr(record, "id", None),
        )
        return {"sent": False, "reason": "error"}
