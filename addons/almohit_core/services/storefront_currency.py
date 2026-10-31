from odoo.http import request


class StorefrontCurrencyError(ValueError):
    pass


def _normalize_currency_code(raw):
    if raw in (None, "", False):
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    text = str(raw).strip().upper()
    return text or None


def active_currency_by_code(env, code):
    """Return an active res.currency by ISO code, or raise StorefrontCurrencyError."""
    code = _normalize_currency_code(code)
    if not code:
        raise StorefrontCurrencyError(
            "X-Currency must contain an active currency code."
        )
    Currency = env["res.currency"].sudo().with_context(active_test=False)
    currency = Currency.search([("name", "=", code)], limit=1)
    if not currency or not currency.active:
        raise StorefrontCurrencyError(
            "X-Currency must contain an active currency code."
        )
    return currency


def requested_currency(env, query_code=None):
    """
    Resolve the optional storefront currency.

    Priority: X-Currency header, then optional query/body code (products only).
    Missing value → None (caller keeps the merchant default pricelist).
    """
    header = None
    if request:
        header = request.httprequest.headers.get("X-Currency")
    header_code = _normalize_currency_code(header)
    if header_code:
        return active_currency_by_code(env, header_code)
    query = _normalize_currency_code(query_code)
    if query:
        return active_currency_by_code(env, query)
    return None


def _company_ok(pricelist, company):
    return not pricelist.company_id or pricelist.company_id.id == company.id


def authorized_currency_pricelists(env, base, currency):
    """
    Same-segment pricelists explicitly linked for Almohit currency switching.

    Links are symmetric via almohit_currency_variant_ids. Never search all
    company pricelists by currency alone.
    """
    Pricelist = env["product.pricelist"].sudo()
    linked = base.almohit_currency_variant_ids | Pricelist.search([
        ("almohit_currency_variant_ids", "in", [base.id]),
        ("company_id", "in", [False, env.company.id]),
    ])
    return linked.filtered(
        lambda row:
            row.active
            and row.currency_id.id == currency.id
            and _company_ok(row, env.company)
    )


def storefront_pricelist(env, partner, currency=None):
    """
    Resolve a server-approved native Odoo pricelist for the merchant.

    Without a requested currency, keep the merchant's assigned pricelist.
    With a currency matching that list, keep it.
    Otherwise require an explicit Almohit currency-variant link on that list.
    """
    base = partner.sudo().property_product_pricelist
    if not base:
        raise StorefrontCurrencyError(
            "No pricelist is assigned to this merchant."
        )
    if not _company_ok(base, env.company) or not base.active:
        raise StorefrontCurrencyError(
            "No pricelist is assigned to this merchant."
        )
    if not currency or base.currency_id.id == currency.id:
        return base

    matches = authorized_currency_pricelists(env, base, currency)
    if len(matches) == 1:
        return matches
    if not matches:
        raise StorefrontCurrencyError(
            "No authorized pricelist for currency %s for this merchant."
            % currency.name
        )
    raise StorefrontCurrencyError(
        "Multiple authorized pricelists for currency %s; "
        "refine Almohit currency variant links on the merchant pricelist."
        % currency.name
    )
