from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError

from .images import SHOP_IMAGE_MAX_BYTES, _decode_image
from .api import error, parse_date_range_params, _query_scalar

MERCHANT_APPLICATION_STATUSES = ("pending", "approved", "rejected")


APPLICATION_ALREADY_PENDING = "APPLICATION_ALREADY_PENDING"
APPLICATION_ALREADY_APPROVED = "APPLICATION_ALREADY_APPROVED"
COMPANY_NAME_REQUIRED = "COMPANY_NAME_REQUIRED"
INVALID_IMAGE = "INVALID_IMAGE"
INVALID_PAYMENT_METHOD = "INVALID_PAYMENT_METHOD"
INVALID_ROLE = "INVALID_ROLE"
PAYMENT_METHODS = {"bank_transfer", "cash", "both"}
APP_ROLES = {"merchant", "buyer", "distributor", "marketer"}
EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
EMAIL_REQUIRED = "EMAIL_REQUIRED"
NAME_REQUIRED = "NAME_REQUIRED"
PASSWORD_TOO_SHORT = "PASSWORD_TOO_SHORT"


def _applications(env):
    # Portal / external users have no ir.model.access on this model.
    # Callers must already have resolved request.env.user.partner_id.
    return env["almohit.trade.application"].sudo()


def own_domain(partner):
    return [("partner_id", "=", partner.id)]


def get_latest_own_application(env, partner):
    return _applications(env).search(
        own_domain(partner),
        order="id desc",
        limit=1,
    )


def pending_own_application(env, partner):
    return _applications(env).search(
        own_domain(partner) + [("status", "=", "pending")],
        limit=1,
    )


def own_application_create_error(env, partner):
    latest = get_latest_own_application(env, partner)
    latest_status = latest.status if latest else False
    partner_status = partner.almohit_trade_status
    if partner_status == "approved" or latest_status == "approved":
        return APPLICATION_ALREADY_APPROVED
    if partner_status == "pending" or latest_status == "pending" or pending_own_application(env, partner):
        return APPLICATION_ALREADY_PENDING
    return None


def serialize_application(application):
    if not application:
        return None
    shop_image_url = None
    if application.shop_image:
        shop_image_url = f"/web/image/almohit.trade.application/{application.id}/shop_image"
    return {
        "id": application.id,
        "status": application.status,
        "role": application.requested_role or "merchant",
        "company_name": application.company_name,
        "tax_number": application.tax_number or None,
        "business_type": application.business_type or None,
        "phone": application.phone or None,
        "street": application.street or None,
        "city": application.city or None,
        "manager_name": application.manager_name or None,
        "employee_name": application.employee_name or None,
        "bank_account": application.bank_account or None,
        "payment_method": application.payment_method or None,
        "shop_image_url": shop_image_url,
        "notes": application.notes or None,
        "rejection_reason": application.rejection_reason or None,
        "created_at": application.create_date,
        "recruited_by": (
            {
                "partner_id": application.recruited_by_id.id,
                "name": application.recruited_by_id.name,
            }
            if application.recruited_by_id
            else None
        ),
        "merchant_email": application.partner_id.email or None,
        "latitude": application.partner_id.partner_latitude or None,
        "longitude": application.partner_id.partner_longitude or None,
    }


def create_own_application(env, partner, payload):
    payload = dict(payload or {})

    # Never trust client identity or workflow fields.
    payload.pop("partner_id", None)
    payload.pop("status", None)
    payload.pop("reviewed_by", None)
    payload.pop("reviewed_at", None)
    payload.pop("rejection_reason", None)
    payload.pop("recruited_by", None)
    payload.pop("recruited_by_id", None)

    blocked = own_application_create_error(env, partner)
    if blocked:
        return None, blocked

    company_name = (payload.get("company_name") or "").strip()
    if not company_name:
        return None, COMPANY_NAME_REQUIRED

    payment_method = (payload.get("payment_method") or "").strip()
    if payment_method and payment_method not in PAYMENT_METHODS:
        return None, INVALID_PAYMENT_METHOD

    role = (
        payload.get("role")
        or partner.almohit_app_role
        or "merchant"
    ).strip().lower()

    if role not in APP_ROLES:
        return None, INVALID_ROLE

    # Validate location coordinates.
    try:
        latitude = (
            float(payload["latitude"])
            if payload.get("latitude") not in (None, "")
            else None
        )

        longitude = (
            float(payload["longitude"])
            if payload.get("longitude") not in (None, "")
            else None
        )

    except (TypeError, ValueError):
        return None, error(
            "INVALID_LOCATION",
            "Latitude and longitude must be valid numbers.",
            400,
        )

    if latitude is not None and not (-90 <= latitude <= 90):
        return None, error(
            "INVALID_LOCATION",
            "Latitude must be between -90 and 90.",
            400,
        )

    if longitude is not None and not (-180 <= longitude <= 180):
        return None, error(
            "INVALID_LOCATION",
            "Longitude must be between -180 and 180.",
            400,
        )

    image_b64, image_err = _decode_image(
        {
            "image_base64": payload.get("shop_image_base64"),
            "image_content_type": payload.get(
                "shop_image_content_type"
            ),
        },
        max_bytes=SHOP_IMAGE_MAX_BYTES,
    )

    if image_err:
        return None, image_err

    values = {
        "partner_id": partner.id,
        "requested_role": role,
        "company_name": company_name,
        "tax_number": (
            payload.get("tax_number") or ""
        ).strip() or False,
        "business_type": (
            payload.get("business_type") or ""
        ).strip() or False,
        "phone": (
            payload.get("phone") or ""
        ).strip() or False,
        "street": (
            payload.get("street")
            or payload.get("address")
            or ""
        ).strip() or False,
        "city": (
            payload.get("city") or ""
        ).strip() or False,
        "manager_name": (
            payload.get("manager_name") or ""
        ).strip() or False,
        "employee_name": (
            payload.get("employee_name") or ""
        ).strip() or False,
        "bank_account": (
            payload.get("bank_account") or ""
        ).strip() or False,
        "payment_method": payment_method or False,
        "notes": (
            payload.get("notes") or ""
        ).strip() or False,
    }

    if image_b64:
        values["shop_image"] = image_b64

    try:
        application = _applications(env).create(values)

    except ValidationError:
        return None, APPLICATION_ALREADY_PENDING

    except UserError:
        return None, error(
            "INVALID_IMAGE_DATA",
            "Image data is invalid.",
            400,
        )

    partner_values = {}

    if partner.almohit_app_role != role:
        partner_values["almohit_app_role"] = role

    if latitude is not None:
        partner_values["partner_latitude"] = latitude

    if longitude is not None:
        partner_values["partner_longitude"] = longitude

    if partner_values:
        partner.write(partner_values)

    return application, None


def _register_portal_user(env, name, email, password, phone, role):
    Users = env["res.users"].sudo().with_context(active_test=False)
    if Users.search([("login", "=", email)], limit=1):
        return None, EMAIL_ALREADY_EXISTS
    company = env.ref("base.main_company", raise_if_not_found=False) or env["res.company"].sudo().search([], limit=1)
    if not company:
        return None, "COMPANY_NOT_CONFIGURED"
    portal_group = env.ref("base.group_portal")
    user = Users.create({
        "name": name,
        "login": email,
        "password": password,
        "company_id": company.id,
        "company_ids": [(6, 0, [company.id])],
        "groups_id": [(6, 0, [portal_group.id])],
    })
    partner = user.partner_id
    partner.write({
        "name": name,
        "email": email,
        "phone": phone or False,
        "almohit_trade_status": "normal",
        "almohit_app_role": role,
    })
    return user, None


def create_recruited_merchant(env, marketer, payload):
    payload = dict(payload or {})
    payload.pop("partner_id", None)
    payload.pop("user_id", None)
    payload.pop("status", None)
    payload.pop("role", None)
    payload.pop("recruited_by", None)
    payload.pop("recruited_by_id", None)
    payload.pop("reviewed_by", None)
    payload.pop("reviewed_at", None)
    payload.pop("rejection_reason", None)

    name = (payload.get("name") or payload.get("company_name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    phone = (payload.get("phone") or "").strip()
    company_name = (payload.get("company_name") or name).strip()
    if not name:
        return None, NAME_REQUIRED
    if not email:
        return None, EMAIL_REQUIRED
    if len(password) < 8:
        return None, PASSWORD_TOO_SHORT
    if not company_name:
        return None, COMPANY_NAME_REQUIRED
    payment_method = (payload.get("payment_method") or "").strip()
    if payment_method and payment_method not in PAYMENT_METHODS:
        return None, INVALID_PAYMENT_METHOD
    image_b64, image_err = _decode_image(
        {
            "image_base64": payload.get("shop_image_base64"),
            "image_content_type": payload.get("shop_image_content_type"),
        },
        max_bytes=SHOP_IMAGE_MAX_BYTES,
    )
    if image_err:
        return None, image_err
    
    try:
        latitude = (
            float(payload["latitude"])
            if payload.get("latitude") not in (None, "")
            else None
        )
        longitude = (
            float(payload["longitude"])
            if payload.get("longitude") not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        return None, error(
            "INVALID_LOCATION",
            "Latitude and longitude must be valid numbers.",
            400,
        )

    if latitude is not None and not (-90 <= latitude <= 90):
        return None, error(
            "INVALID_LOCATION",
            "Latitude must be between -90 and 90.",
            400,
        )

    if longitude is not None and not (-180 <= longitude <= 180):
        return None, error(
            "INVALID_LOCATION",
            "Longitude must be between -180 and 180.",
            400,
        )
    user, code = _register_portal_user(env, name, email, password, phone, "merchant")
    if code:
        return None, code
    partner = user.partner_id

    partner_vals = {
        "almohit_recruited_by_id": marketer.id,
    }

    if latitude is not None:
        partner_vals["partner_latitude"] = latitude

    if longitude is not None:
        partner_vals["partner_longitude"] = longitude

    partner.write(partner_vals)
    
    values = {
        "partner_id": partner.id,
        "requested_role": "merchant",
        "recruited_by_id": marketer.id,
        "company_name": company_name,
        "tax_number": (payload.get("tax_number") or "").strip() or False,
        "business_type": (payload.get("business_type") or "").strip() or False,
        "phone": phone or False,
        "street": (payload.get("street") or payload.get("address") or "").strip() or False,
        "city": (payload.get("city") or "").strip() or False,
        "manager_name": (payload.get("manager_name") or "").strip() or False,
        "employee_name": (payload.get("employee_name") or "").strip() or False,
        "bank_account": (payload.get("bank_account") or "").strip() or False,
        "payment_method": payment_method or False,
        "notes": (payload.get("notes") or "").strip() or False,
    }
    if image_b64:
        values["shop_image"] = image_b64
    try:
        application = _applications(env).create(values)
    except ValidationError:
        return None, APPLICATION_ALREADY_PENDING
    except UserError:
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    # Approved marketers auto-approve their recruited merchants using the
    # same action_approve() path as staff review (pricelist, role, status,
    # and a single trade_approved notification). env.user is the portal
    # marketer; su=True only bypasses model ACL. reviewed_by records that
    # marketer user, not a synthetic staff account.
    try:
        application.with_env(env(user=env.user, su=True)).action_approve()
    except UserError:
        return None, APPLICATION_NOT_PENDING
    return application, None


def search_recruited_merchants(env, marketer):
    return _applications(env).search(
        [("recruited_by_id", "=", marketer.id)],
        order="id desc",
    )


def parse_merchant_list_params(kwargs):
    extra = []
    raw_q = _query_scalar(kwargs, "q")
    q = str(raw_q).strip() if raw_q not in (None, "") else ""
    if q:
        extra += [
            "|",
            "|",
            "|",
            ("company_name", "ilike", q),
            ("tax_number", "ilike", q),
            ("phone", "ilike", q),
            ("partner_id.email", "ilike", q),
        ]

    raw_status = _query_scalar(kwargs, "status")
    if raw_status not in (None, ""):
        status = str(raw_status).strip().lower()
        if status not in MERCHANT_APPLICATION_STATUSES:
            return None, None, error(
                "INVALID_FILTER",
                "status must be one of: pending, approved, rejected.",
                400,
            )
        extra.append(("status", "=", status))

    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return None, None, err
    if date_from:
        extra.append(
            ("create_date", ">=", datetime.combine(date_from, time.min))
        )
    if date_to:
        extra.append(
            ("create_date", "<=", datetime.combine(date_to, time(23, 59, 59)))
        )

    order = "id desc"
    raw_sort = _query_scalar(kwargs, "sort")
    if raw_sort not in (None, ""):
        sort = str(raw_sort).strip().lower()
        if sort == "newest":
            order = "create_date desc, id desc"
        elif sort == "oldest":
            order = "create_date asc, id asc"
        else:
            return None, None, error(
                "INVALID_SORT",
                "sort must be one of: newest, oldest.",
                400,
            )
    return extra, order, None


def list_recruited_merchants(env, marketer):
    return [
        serialize_application(record)
        for record in search_recruited_merchants(env, marketer)
    ]


APPLICATION_NOT_FOUND = "APPLICATION_NOT_FOUND"
APPLICATION_NOT_PENDING = "APPLICATION_NOT_PENDING"
