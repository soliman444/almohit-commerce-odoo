# -*- coding: utf-8 -*-
import json
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

OPEN_ER_URL = "https://open.er-api.com/v6/latest/{base}"
CURRENCYAPI_URL = "https://api.currencyapi.com/v3/latest"


class CustomCurrencyRateSync(models.Model):
    _name = "custom.currency.rate.sync"
    _description = "Standalone currency rate synchronization"
    _rec_name = "name"

    name = fields.Char(
        string="Label",
        required=True,
        default="Currency rate sync",
        help="Used to distinguish several sync setups for the same company.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    active = fields.Boolean(default=True)
    auto_sync = fields.Boolean(
        string="Scheduled sync (daily cron)",
        default=True,
        help="If enabled, rates are refreshed once per day by the scheduled action.",
    )
    rate_provider = fields.Selection(
        [
            ("open_er", "ExchangeRate-API (open, no API key)"),
            ("currencyapi", "CurrencyAPI.com"),
        ],
        string="Provider",
        default="open_er",
        required=True,
    )
    currencyapi_api_key = fields.Char(
        string="CurrencyAPI API key",
        help="Required for CurrencyAPI.com. Create a key at https://currencyapi.com — use the header "
        "style key (recommended). Never share your key publicly.",
    )
    currency_ids = fields.Many2many(
        "res.currency",
        "custom_currency_rate_sync_currency_rel",
        "sync_id",
        "currency_id",
        string="Currencies to update",
        help="Foreign currencies to sync (company currency is ignored).",
        domain=[("active", "=", True)],
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company currency",
    )
    last_sync = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)

    def action_sync_now(self):
        self.ensure_one()
        self._sync_rates()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Currency rates"),
                "message": _("Rates have been updated for today."),
                "type": "success",
                "sticky": False,
            },
        }

    def _rates_from_open_er(self, base_code):
        url = OPEN_ER_URL.format(base=quote(base_code, safe=""))
        with urlopen(url, timeout=25) as response:
            payload = json.loads(response.read().decode())
        if payload.get("result") != "success":
            err = payload.get("error-type") or payload.get("error_type") or "error"
            raise UserError(_("ExchangeRate-API error: %s") % err)
        rates = payload.get("rates") or {}
        if base_code not in rates:
            raise UserError(
                _("Base currency %s is not supported by ExchangeRate-API.") % base_code
            )
        return rates

    def _rates_from_currencyapi(self, base_code):
        self.ensure_one()
        key = (self.currencyapi_api_key or "").strip()
        if not key:
            raise UserError(
                _("Please set the CurrencyAPI API key on this configuration.")
            )
        # Official docs recommend sending the key in the apikey header (avoids keys in server logs).
        params = f"?base_currency={quote(base_code, safe='')}"
        url = CURRENCYAPI_URL + params
        req = Request(url, headers={"apikey": key})
        with urlopen(req, timeout=25) as response:
            payload = json.loads(response.read().decode())

        errs = payload.get("errors") or payload.get("error")
        if errs:
            raise UserError(_("CurrencyAPI error: %s") % (errs,))

        data = payload.get("data") or {}
        rates = {}
        for code, info in data.items():
            if not isinstance(info, dict):
                continue
            val = info.get("value")
            if val is None:
                continue
            rates[code] = val
        if base_code not in rates:
            raise UserError(
                _("Base currency %s is not returned by CurrencyAPI for this request.")
                % base_code
            )
        return rates

    def _sync_rates(self):
        self.ensure_one()
        company = self.company_id.sudo()
        root = company.root_id
        base = company.currency_id
        if not base:
            raise UserError(_("Company has no currency set."))
        base_code = base.name
        to_fetch = self.currency_ids.filtered(lambda c: c != base)
        if not to_fetch:
            raise UserError(_("Select at least one foreign currency."))

        try:
            if self.rate_provider == "currencyapi":
                rates_raw = self._rates_from_currencyapi(base_code)
            else:
                rates_raw = self._rates_from_open_er(base_code)
        except (HTTPError, URLError, TimeoutError, ValueError) as e:
            msg = str(e)
            self.write({"last_error": msg, "last_sync": fields.Datetime.now()})
            raise UserError(_("Could not fetch rates: %s") % msg) from e

        rate_date = date.today()
        Rate = self.env["res.currency.rate"].sudo()
        updated = 0
        errors = []

        for currency in to_fetch:
            code = currency.name
            if code not in rates_raw:
                errors.append(_("%s: not in API response") % code)
                continue
            try:
                val = float(rates_raw[code])
            except (TypeError, ValueError):
                errors.append(_("%s: invalid rate") % code)
                continue
            if val <= 0:
                errors.append(_("%s: non-positive rate") % code)
                continue

            existing = Rate.search(
                [
                    ("company_id", "=", root.id),
                    ("currency_id", "=", currency.id),
                    ("name", "=", rate_date),
                ],
                limit=1,
            )
            vals = {
                "company_id": root.id,
                "currency_id": currency.id,
                "name": rate_date,
                "rate": val,
            }
            if existing:
                existing.write(vals)
            else:
                Rate.create(vals)
            updated += 1

        note = _("Updated %(n)s rate(s) for %(date)s.") % {
            "n": updated,
            "date": rate_date,
        }
        if errors:
            note += "\n" + "\n".join(errors)
        self.write(
            {
                "last_sync": fields.Datetime.now(),
                "last_error": note if errors else False,
            }
        )
        _logger.info("custom.currency.rate.sync: %s", note)

    @api.model
    def cron_sync_rates(self):
        configs = self.search([("active", "=", True), ("auto_sync", "=", True)])
        for config in configs:
            try:
                config._sync_rates()
            except Exception as err:
                _logger.exception(
                    "Currency sync failed (%s / %s)",
                    config.name,
                    config.company_id.name,
                )
                config.write(
                    {
                        "last_sync": fields.Datetime.now(),
                        "last_error": str(err),
                    }
                )
