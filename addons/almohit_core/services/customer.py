from odoo.http import request, root

from .api import error


def apply_bearer_session():
    """Accept Authorization: Bearer <session_id> in addition to the cookie."""
    if request.session.uid:
        return
    header = request.httprequest.headers.get("Authorization") or ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return
    sid = parts[1].strip()
    if not sid:
        return
    try:
        session = root.session_store.get(sid)
    except Exception:
        return
    if not session or not getattr(session, "uid", None):
        return
    request.session.update(session)
    request.session.sid = sid
    request.update_env(user=session.uid)


def require_api_user():
    apply_bearer_session()
    user = request.env.user
    if not request.session.uid or user._is_public():
        return None, error(
            "UNAUTHENTICATED",
            "Sign in required.",
            401,
        )
    return user, None


def require_customer_partner():
    user, err = require_api_user()
    if err:
        return None, err
    if not user.share:
        return None, error(
            "CUSTOMER_REQUIRED",
            "Only customers can access this resource.",
            403,
        )
    partner = user.partner_id
    if not partner:
        return None, error(
            "PARTNER_REQUIRED",
            "No customer partner is linked to this user.",
            400,
        )
    # Portal users lack ACL on res.partner children / sale.order writes.
    # sudo() is applied after identity is taken from the session user.
    return partner.sudo(), None


def require_approved_customer():
    partner, err = require_customer_partner()
    if err:
        return None, err
    if partner.almohit_trade_status != "approved":
        return None, error(
            "WHOLESALE_APPROVAL_REQUIRED",
            "Wholesale approval is required to place an order.",
            403,
        )
    if partner_app_role(partner) == "marketer":
        return None, error(
            "MARKETER_CANNOT_ORDER",
            "Marketers cannot place orders.",
            403,
        )
    return partner, None


def require_distributor_partner():
    partner, err = require_approved_customer()
    if err:
        return None, err
    if partner_app_role(partner) != "distributor":
        return None, error(
            "DISTRIBUTOR_REQUIRED",
            "Only distributors can access deliveries.",
            403,
        )
    return partner, None


def require_marketer_partner():
    partner, err = require_customer_partner()
    if err:
        return None, err
    if partner_app_role(partner) != "marketer":
        return None, error(
            "MARKETER_REQUIRED",
            "Only marketers can access recruited merchants.",
            403,
        )
    if partner.almohit_trade_status != "approved":
        return None, error(
            "WHOLESALE_APPROVAL_REQUIRED",
            "Marketer approval is required.",
            403,
        )
    return partner, None


def commercial_partner(partner):
    return partner.commercial_partner_id or partner


def serialize_payment_term(term):
    """Native account.payment.term only. Never invent frontend term codes."""
    if not term:
        return None
    return {
        "id": term.id,
        "name": term.name,
    }


def partner_payment_term(partner):
    return serialize_payment_term(partner.property_payment_term_id)


def partner_app_role(partner):
    return partner.almohit_app_role or "merchant"


def serialize_price_group(partner):
    pricelist = partner.sudo().property_product_pricelist
    if not pricelist:
        return None
    return {
        "id": pricelist.id,
        "name": pricelist.name,
    }


def current_session_id():
    if not request.session.uid:
        return None
    return request.session.sid or None


def serialize_auth_user(user, partner=None, include_session=False):
    partner = partner or user.partner_id
    payload = {
        "user_id": user.id,
        "partner_id": partner.id,
        "name": partner.name,
        "email": partner.email,
        "phone": partner.phone or None,
        "trade_status": partner.almohit_trade_status,
        "role": partner_app_role(partner),
        "payment_term": partner_payment_term(partner.sudo()),
        "price_group": serialize_price_group(partner),
        "is_staff": not bool(user.share),
    }
    if include_session:
        payload["session_id"] = current_session_id()
    return payload
