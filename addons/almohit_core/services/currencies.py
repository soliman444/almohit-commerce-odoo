from odoo import fields


def list_display_currencies(env):
    company = env.company.sudo()
    company_currency = company.currency_id
    today = fields.Date.context_today(company)
    currencies = env["res.currency"].sudo().search(
        [("active", "=", True)],
        order="name",
    )
    rows = []
    for currency in currencies:
        rate = company_currency._convert(1.0, currency, company, today, round=False)
        rows.append({
            "id": currency.id,
            "code": currency.name,
            "name": _currency_name(currency),
            "symbol": currency.symbol or currency.name,
            "decimal_places": currency.decimal_places,
            "rate_from_company": float(rate),
            "is_company": currency.id == company_currency.id,
        })
    rows.sort(key=lambda row: (not row["is_company"], row["code"]))
    return {
        "company_currency": company_currency.name,
        "date": fields.Date.to_string(today),
        "currencies": rows,
    }


def resolve_display_currency(env, code):
    """Resolve an active currency by ISO code.

    Empty/missing code → None (caller keeps legacy company labeling).
    Unknown code → False (caller should return 400).
    """
    if code in (None, "", False):
        return None
    if isinstance(code, (list, tuple)):
        code = code[0] if code else ""
    text = str(code).strip().upper()
    if not text:
        return None
    currency = env["res.currency"].sudo().search(
        [("name", "=", text), ("active", "=", True)],
        limit=1,
    )
    return currency if currency else False


def convert_price_map(env, prices, from_currency, to_currency):
    """Convert a product-id → amount map between currencies via Odoo rates."""
    if not prices:
        return {}
    if not from_currency or not to_currency or from_currency.id == to_currency.id:
        return dict(prices)
    company = env.company.sudo()
    today = fields.Date.context_today(company)
    converted = {}
    for key, raw in prices.items():
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            continue
        converted[key] = from_currency._convert(
            amount, to_currency, company, today,
        )
    return converted


def _currency_name(currency):
    if "full_name" in currency._fields and currency.full_name:
        return currency.full_name
    return currency.name
