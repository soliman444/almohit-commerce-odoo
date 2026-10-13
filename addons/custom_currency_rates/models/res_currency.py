# -*- coding: utf-8 -*-
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Frankfurter v2 (see https://www.frankfurter.app/docs/). v1 on api.frankfurter.app often returns 404.
FRANKFURTER_API_BASES = ("https://api.frankfurter.dev/v2",)
FRANKFURTER_CHUNK = 40
# If currency + system parameter margins are 0, apply this board spread so buy ≠ sell on fetch.
FRANKFURTER_FALLBACK_MARGIN_PERCENT = 2.0


class ResCurrency(models.Model):
    _inherit = "res.currency"

    travel_auto_update = fields.Boolean(
        string="Travel auto-update rates",
        help="If enabled, the scheduled action refreshes this currency's rates via Frankfurter for each company where it is not the company currency.",
    )
    travel_sell_margin_percent = fields.Float(
        string="Default travel sell margin %",
        digits=(16, 4),
        default=0.0,
        help="Markup from the accounting (buy) rate to the board / customer sell rate. "
        "When fetching Frankfurter, the API rate is written to Odoo company_rate (buy); "
        "travel_sell_rate = company_rate × (1 + margin/100). If zero here, the system "
        "parameter custom_currency_rates.travel_default_sell_margin_percent is used.",
    )

    def _effective_travel_sell_margin_percent(self, currency):
        """Margin % used on fetch: currency value, else optional system default."""
        m = currency.travel_sell_margin_percent or 0.0
        if m:
            return m
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("custom_currency_rates.travel_default_sell_margin_percent", "0")
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def _frankfurter_fetch_json(self, path_query):
        """Frankfurter (and many CDNs) return 403 for Python's default urllib User-Agent."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; OdooCustomCurrencyRates/17; "
                "+https://www.odoo.com/)"
            ),
            "Accept": "application/json",
        }
        last_error = None
        for api_base in FRANKFURTER_API_BASES:
            url = f"{api_base}/{path_query}"
            req = Request(url, headers=headers)
            try:
                with urlopen(req, timeout=40) as response:
                    return json.loads(response.read().decode())
            except HTTPError as e:
                last_error = e
                if e.code in (403, 502, 503, 504):
                    _logger.warning(
                        "Frankfurter HTTP %s on %s, trying next host if any",
                        e.code,
                        api_base,
                    )
                    continue
                raise
        if last_error:
            raise last_error
        raise UserError(_("No Frankfurter API host could be reached."))

    @staticmethod
    def _frankfurter_http_error_detail(exc):
        try:
            body = exc.read().decode()
            data = json.loads(body)
            return data.get("message") or body
        except Exception:
            return str(exc) or getattr(exc, "reason", "") or repr(exc)

    @staticmethod
    def _parse_frankfurter_v2_rates_payload(payload):
        """v2 returns a JSON array of {date, base, quote, rate}."""
        if not isinstance(payload, list) or not payload:
            raise UserError(_("Frankfurter returned an empty or invalid response."))
        provider_date = None
        rates = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            raw_d = row.get("date")
            if raw_d and provider_date is None:
                provider_date = fields.Date.to_date(raw_d)
            quote = row.get("quote")
            rate = row.get("rate")
            if quote is None or rate is None:
                continue
            try:
                rates[str(quote).upper()] = float(rate)
            except (TypeError, ValueError):
                continue
        return provider_date, rates

    def _frankfurter_rates_for_chunk(self, base_code, chunk_codes):
        """Fetch one batch; on HTTP 422 split batch to isolate invalid ISO codes."""
        if not chunk_codes:
            return None, {}
        # ISO 4217 codes from Odoo are safe in URL path segment
        quotes = ",".join(chunk_codes)
        query = f"rates?base={quote(base_code, safe='')}&quotes={quotes}"
        try:
            payload = self._frankfurter_fetch_json(query)
        except HTTPError as e:
            detail = self._frankfurter_http_error_detail(e)
            if e.code == 422 and len(chunk_codes) > 1:
                mid = len(chunk_codes) // 2
                d1, r1 = self._frankfurter_rates_for_chunk(base_code, chunk_codes[:mid])
                d2, r2 = self._frankfurter_rates_for_chunk(base_code, chunk_codes[mid:])
                return d1 or d2, {**r1, **r2}
            if e.code == 422 and len(chunk_codes) == 1:
                _logger.warning(
                    "Frankfurter rejected currency %s: %s", chunk_codes[0], detail
                )
                return None, {}
            if e.code == 404:
                raise UserError(
                    _(
                        "Frankfurter returned HTTP 404: %(msg)s. "
                        "Check that company currency %(ccy)s and selected currencies exist in Frankfurter.",
                        msg=detail,
                        ccy=base_code,
                    )
                ) from e
            raise UserError(_("Frankfurter error (HTTP %(code)s): %(msg)s") % {"code": e.code, "msg": detail}) from e
        return self._parse_frankfurter_v2_rates_payload(payload)

    def _frankfurter_rates_for_targets(self, base_code, target_codes):
        """Return (provider_date: date, rates: {code: float})."""
        clean = []
        seen = set()
        for code in target_codes:
            c = (code or "").strip().upper()
            if len(c) == 3 and c.isalpha() and c not in seen:
                seen.add(c)
                clean.append(c)
        if not clean:
            return None, {}
        rates = {}
        provider_date = None
        for i in range(0, len(clean), FRANKFURTER_CHUNK):
            chunk = clean[i : i + FRANKFURTER_CHUNK]
            pdate, part = self._frankfurter_rates_for_chunk(base_code, chunk)
            if provider_date is None and pdate:
                provider_date = pdate
            rates.update(part)
        return provider_date, rates

    def _travel_update_rates_for_company(self, company, currencies):
        """Update today's rates: company_rate (buy) = API mid; travel_sell_rate = buy × (1 + margin%)."""
        root = company.sudo().root_id
        base = company.currency_id
        if not base:
            raise UserError(_("Company %s has no currency.") % company.name)
        base_code = base.name
        to_fetch = currencies.filtered(lambda c: c != base and c.active)
        if not to_fetch:
            return 0, []

        codes = [c.name for c in to_fetch]
        try:
            provider_date, rates_raw = self._frankfurter_rates_for_targets(base_code, codes)
        except (HTTPError, URLError, TimeoutError, ValueError, UserError) as e:
            raise UserError(_("Could not fetch Frankfurter rates: %s") % e) from e

        rate_date = fields.Date.context_today(self.with_company(company))
        fetch_dt = fields.Datetime.now()
        Rate = self.env["res.currency.rate"].sudo()
        updated = 0
        errors = []

        for currency in to_fetch:
            code = currency.name
            if code not in rates_raw:
                errors.append(_("%s: not in Frankfurter response") % code)
                continue
            val = rates_raw[code]
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
            # API mid = purchase / accounting (Odoo company_rate). Sell = buy × (1 + margin%).
            buy_rate = float(val)
            margin_pct = self._effective_travel_sell_margin_percent(currency)
            if margin_pct <= 0:
                margin_pct = FRANKFURTER_FALLBACK_MARGIN_PERCENT
            m = margin_pct / 100.0
            sell_rate = buy_rate * (1.0 + m)
            vals = {
                "company_id": root.id,
                "currency_id": currency.id,
                "name": rate_date,
                "company_rate": buy_rate,
                "travel_sell_margin_percent": margin_pct,
                "travel_sell_rate": sell_rate,
                "travel_provider": "frankfurter",
                "travel_provider_rate_date": provider_date,
                "travel_last_fetch_datetime": fetch_dt,
            }
            if existing:
                existing.write(vals)
            else:
                Rate.create(vals)
            updated += 1

        return updated, errors

    def action_recompute_travel_sell_rates(self):
        """Recompute Travel sell rate / margin on all stored rate lines (skips Frankfurter rows)."""
        rates = self.mapped("rate_ids").filtered(lambda r: not r.travel_provider)
        rates._sync_travel_sell_from_buy()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Travel sell rates"),
                "message": _("Recomputed %(n)s rate line(s).") % {"n": len(rates)},
                "type": "success",
                "sticky": False,
            },
        }

    def action_fetch_frankfurter_travel_rates(self):
        """Action / button: refresh rates for selected currencies (per current company)."""
        company = self.env.company.sudo()
        currencies = self.filtered(lambda c: c.active and c != company.currency_id)
        if not currencies:
            raise UserError(_("Select at least one foreign currency (not the company currency)."))
        updated, errors = currencies._travel_update_rates_for_company(company, currencies)
        parts = [
            _("Updated %(n)s rate(s) for %(company)s.")
            % {"n": updated, "company": company.name}
        ]
        if errors:
            parts.append("\n".join(errors))
        msg = "\n".join(parts)
        _logger.info("Frankfurter travel rates: %s", msg)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Travel currency rates"),
                "message": msg,
                "type": "success" if not errors else "warning",
                "sticky": bool(errors),
            },
        }

    @api.model
    def cron_travel_fetch_frankfurter_rates(self):
        """Scheduled: for every company, update currencies flagged with travel_auto_update."""
        companies = self.env["res.company"].sudo().search([])
        Currency = self.env["res.currency"]
        for company in companies:
            base = company.currency_id
            if not base:
                continue
            to_update = Currency.search(
                [
                    ("active", "=", True),
                    ("travel_auto_update", "=", True),
                    ("id", "!=", base.id),
                ]
            )
            if not to_update:
                continue
            try:
                n, errors = to_update._travel_update_rates_for_company(company, to_update)
                note = _("Company %(c)s: updated %(n)s rate(s).") % {"c": company.name, "n": n}
                if errors:
                    note += " " + " ".join(errors)
                _logger.info(note)
            except Exception:
                _logger.exception(
                    "Travel Frankfurter cron failed for company %s", company.name
                )
