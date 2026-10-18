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
import time
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
    return whatsapp_session_info(url=url, timeout=timeout)["status_text"]


def _http_json(method, url, token="", timeout=4, payload=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer %s" % token
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode("utf-8") or "{}")
        except Exception:
            return {}
    except Exception:
        return {}


_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._\-]+|data:[^;]+;base64,[A-Za-z0-9+/=\s]+"
)
_SESSION_STATE = {}
_DISCONNECTED_STATUSES = {
    "qr",
    "notlogged",
    "unpaired",
    "unpairedidle",
    "disconnected",
    "disconnectedmobile",
    "browserclose",
    "stopped",
    "waitingqr",
    "waiting_qr",
}
_SESSION_LOST_STATUSES = {
    "notlogged",
    "unpaired",
    "unpairedidle",
    "disconnected",
    "disconnectedmobile",
    "browserclose",
}
_SERVICE_ERROR_CODES = {"UNAUTHORIZED"}
_SESSION_ERROR_CODES = {"UNLINK_FAILED", "INTERNAL_ERROR"}
_FALLBACK_STATUS = {
    "Not configured": "not_configured",
    "Service token rejected": "unauthorized",
    "Service unreachable": "unreachable",
    "Could not unlink the WhatsApp account.": "unlink_failed",
}


def _compact_status(value):
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def _safe_log_text(value, limit=250):
    text = _SECRET_RE.sub("[redacted]", str(value or "").strip())
    if len(text) > limit:
        return text[:limit]
    return text


def _session_db_state(env):
    if env is None:
        return None
    return _SESSION_STATE.setdefault(
        env.cr.dbname,
        {"fp": None, "connected": False, "unlink_pending": False},
    )


def _error_category(info):
    code = str(info.get("error_code") or "")
    raw = _compact_status(info.get("raw_status"))
    if code in _SERVICE_ERROR_CODES or raw in ("unauthorized", "notconfigured", "unreachable"):
        return "service_error"
    if code in _SESSION_ERROR_CODES or raw in ("error", "unlinkfailed"):
        return "session_error"
    if info.get("ok"):
        return None
    if raw == "error" or code:
        return "session_error" if code in _SESSION_ERROR_CODES or raw == "error" else "service_error"
    return "service_error"


def _session_fingerprint(info):
    err = _error_category(info)
    connected = bool(info.get("connected"))
    has_qr = bool(info.get("qr"))
    if err:
        return (False, err, False, str(info.get("error_code") or info.get("raw_status") or err))
    if connected:
        return (True, "connected", False, None)
    raw = _compact_status(info.get("raw_status"))
    waiting = has_qr or raw in ("waitingqr", "waiting_qr", "notlogged", "qr")
    if waiting:
        return (False, "waiting_qr", has_qr, None)
    if raw in _DISCONNECTED_STATUSES:
        return (False, "disconnected", False, None)
    return (False, raw or "unknown", has_qr, None)


def _session_event_type(info, state):
    err = _error_category(info)
    if err:
        return err
    connected = bool(info.get("connected"))
    if state.get("unlink_pending") and not connected:
        return "unlinked"
    if connected:
        return "connected"
    raw = _compact_status(info.get("raw_status"))
    if state.get("connected"):
        if raw in _SESSION_LOST_STATUSES:
            return "session_lost"
        return "disconnected"
    if info.get("qr"):
        return "qr_ready"
    if raw in _DISCONNECTED_STATUSES:
        return "disconnected"
    return None


def _create_session_log(env, event_type, info, phone=""):
    if env is None or not event_type:
        return
    try:
        with env.cr.savepoint():
            env["almohit.whatsapp.session.log"].sudo().create({
                "event_type": event_type,
                "status": _safe_log_text(info.get("raw_status") or info.get("error_code") or "", 64),
                "message": _safe_log_text(info.get("status_text") or "", 250),
                "connected": bool(info.get("connected")),
                "phone": _safe_log_text(phone or info.get("phone") or "", 32),
            })
    except Exception:
        _logger.exception("whatsapp session log failed event=%s", event_type)


def _record_session_snapshot(env, info):
    if env is None or not isinstance(info, dict):
        return
    state = _session_db_state(env)
    if state is None:
        return
    fingerprint = _session_fingerprint(info)
    if fingerprint == state["fp"]:
        return
    event_type = _session_event_type(info, state)
    phone = (info.get("phone") or "").strip()
    if not phone:
        phone = (whatsapp_config(env).get("account_phone") or "").strip()
    if event_type:
        _create_session_log(env, event_type, info, phone)
    state["fp"] = fingerprint
    state["connected"] = bool(info.get("connected"))
    if event_type == "unlinked" or info.get("connected"):
        state["unlink_pending"] = False


def _record_unlink_requested(env):
    if env is None:
        return
    state = _session_db_state(env)
    if state is not None:
        state["unlink_pending"] = True
    cfg = whatsapp_config(env)
    _create_session_log(
        env,
        "unlink_requested",
        {
            "raw_status": "",
            "error_code": "",
            "status_text": "Unlink requested.",
            "connected": False,
            "phone": cfg.get("account_phone") or "",
        },
        cfg.get("account_phone") or "",
    )


def _normalize_qr(raw):
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if value.lower().startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    compact = re.sub(r"[^A-Za-z0-9+/=]", "", value)
    return compact or None


def qr_img_html(qr):
    if not qr:
        return False
    return (
        '<div class="mt-2">'
        '<img src="data:image/png;base64,%s" alt="WhatsApp QR" width="260" height="260" '
        'style="width:260px;height:260px;background:#fff;padding:8px;'
        'border:1px solid #ddd;border-radius:8px"/>'
        "</div>"
    ) % qr


def _session_from_body(body, fallback="Service unreachable"):
    if not isinstance(body, dict):
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    error_code = error.get("code") if isinstance(error, dict) else None
    empty = {
        "ok": False,
        "connected": False,
        "status_text": fallback,
        "phone": "",
        "qr": None,
        "raw_status": error_code or _FALLBACK_STATUS.get(fallback, "unreachable"),
        "error_code": error_code,
    }
    if not isinstance(body, dict) or not body.get("ok"):
        if isinstance(error, dict) and error.get("message"):
            empty["status_text"] = error.get("message")
        return empty
    session = body.get("whatsapp") if isinstance(body.get("whatsapp"), dict) else {}
    connected = bool(session.get("connected"))
    status = session.get("status") or "unknown"
    name = session.get("session") or ""
    phone = (session.get("phone") or "").strip()
    qr = None if connected else _normalize_qr(body.get("qr") or session.get("qr"))
    if connected:
        label = phone or name
        status_text = "Connected%s" % ((" (%s)" % label) if label else "")
    else:
        status_text = "Disconnected (%s)" % status
    return {
        "ok": True,
        "connected": connected,
        "status_text": status_text,
        "phone": phone,
        "qr": qr,
        "raw_status": status,
        "error_code": None,
    }


def whatsapp_session_info(env=None, url=None, timeout=6):
    """Live session for the Odoo account screen. Never required for business success."""
    cfg = whatsapp_config(env)
    base = (url or cfg["url"] or "").strip().rstrip("/")
    token = cfg["token"]
    if not base:
        info = _session_from_body({}, "Not configured")
        _record_session_snapshot(env, info)
        return info
    if token:
        body = _http_json("GET", base + "/session", token, timeout=timeout)
        info = _session_from_body(body)
        if info.get("ok"):
            _record_session_snapshot(env, info)
            return info
        if (body.get("error") or {}).get("code") == "UNAUTHORIZED":
            info = _session_from_body(body, "Service token rejected")
            _record_session_snapshot(env, info)
            return info
    health = _http_json("GET", base + "/health", timeout=min(timeout, 3))
    info = _session_from_body(health, "Service unreachable")
    _record_session_snapshot(env, info)
    return info


def whatsapp_unlink_session(env, timeout=20):
    """Disconnect the current WhatsApp Web session and wait briefly for a new QR."""
    cfg = whatsapp_config(env)
    if not cfg["url"] or not cfg["token"]:
        info = _session_from_body({}, "Not configured")
        info["status_text"] = "WhatsApp service URL or token is missing."
        _record_session_snapshot(env, info)
        return None, "WhatsApp service URL or token is missing."
    _record_unlink_requested(env)
    body = _http_json(
        "POST",
        cfg["url"] + "/unlink",
        cfg["token"],
        timeout=timeout,
        payload={},
    )
    info = _session_from_body(body, "Could not unlink the WhatsApp account.")
    if not info.get("ok"):
        _record_session_snapshot(env, info)
        return None, info["status_text"]
    _record_session_snapshot(env, info)
    if not info.get("qr") and not info.get("connected"):
        for _ in range(8):
            time.sleep(1)
            info = whatsapp_session_info(env, timeout=4)
            if info.get("qr") or info.get("connected"):
                break
    return info, None


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
