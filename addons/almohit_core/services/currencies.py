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


def _currency_name(currency):
    if "full_name" in currency._fields and currency.full_name:
        return currency.full_name
    return currency.name
