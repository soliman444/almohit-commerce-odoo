from .customer import partner_payment_term, serialize_price_group

TRADE_STATUSES = {"normal", "pending", "approved", "rejected"}
APP_ROLES = {"merchant", "buyer", "distributor", "marketer"}


def admin_customer_domain():
    return [("user_ids.share", "=", True)]


def serialize_admin_customer(partner):
    user = partner.user_ids.filtered(lambda rec: rec.share)[:1]
    return {
        "id": partner.id,
        "user_id": user.id if user else None,
        "name": partner.name,
        "email": partner.email or (user.login if user else None),
        "phone": partner.phone or None,
        "trade_status": partner.almohit_trade_status,
        "role": partner.almohit_app_role or "merchant",
        "city": partner.city or None,
        "price_group": serialize_price_group(partner),
        "payment_term": partner_payment_term(partner),
        "created_at": partner.create_date,
        "active": partner.active,
    }


def list_admin_customers(env, params=None):
    params = params or {}
    domain = admin_customer_domain()
    status = (params.get("status") or "").strip().lower()
    if status in TRADE_STATUSES:
        domain.append(("almohit_trade_status", "=", status))
    role = (params.get("role") or "").strip().lower()
    if role in APP_ROLES:
        domain.append(("almohit_app_role", "=", role))
    query = (params.get("q") or "").strip()
    if query:
        domain.extend([
            "|",
            "|",
            ("name", "ilike", query),
            ("email", "ilike", query),
            ("phone", "ilike", query),
        ])
    partners = env["res.partner"].with_context(active_test=False).search(
        domain,
        order="id desc",
        limit=100,
    )
    return [serialize_admin_customer(partner) for partner in partners]


def admin_customer_summary(env):
    return {
        "total": env["res.partner"].with_context(active_test=False).search_count(
            admin_customer_domain(),
        ),
    }


def admin_staff_domain():
    return [("share", "=", False)]


def serialize_admin_staff(user):
    groups = user.groups_id.filtered(lambda group: group.category_id).mapped("name")
    return {
        "id": user.id,
        "name": user.name,
        "login": user.login,
        "email": user.email or user.login,
        "active": user.active,
        "groups": groups[:8],
    }


def list_admin_staff(env, params=None):
    params = params or {}
    domain = list(admin_staff_domain())
    query = (params.get("q") or "").strip()
    if query:
        domain.extend([
            "|",
            "|",
            ("name", "ilike", query),
            ("login", "ilike", query),
            ("email", "ilike", query),
        ])
    users = env["res.users"].with_context(active_test=False).search(
        domain,
        order="name",
        limit=100,
    )
    return [serialize_admin_staff(user) for user in users]


def admin_staff_summary(env):
    return {
        "total": env["res.users"].with_context(active_test=False).search_count(
            admin_staff_domain(),
        ),
    }
