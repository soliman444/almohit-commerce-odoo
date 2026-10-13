# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools import float_compare


SKIP_TRAVEL_SELL_SYNC_CTX = "custom_currency_rates_skip_travel_sell_sync"
# When line/currency/ICP margins are all zero, use this so Travel sell ≠ Unit per USD.
TRAVEL_SELL_MARGIN_FALLBACK_PERCENT = 2.0


class ResCurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    travel_sell_margin_percent = fields.Float(
        string="Travel sell margin %",
        digits=(16, 4),
        default=0.0,
        help="Markup from Odoo company_rate (buy/accounting) to the board / customer sell rate.",
    )
    travel_sell_rate = fields.Float(
        string="Travel sell rate",
        digits=(12, 12),
        help="Board / customer sell rate = company_rate (buy) × (1 + margin/100). "
        "On Frankfurter fetch, company_rate is the API rate and this field is computed with margin. "
        "For manual rows you can edit this field unless the line is provider-locked.",
    )
    travel_provider = fields.Selection(
        [("frankfurter", "Frankfurter (ECB)")],
        string="Travel rate provider",
        readonly=True,
        copy=False,
    )
    travel_provider_rate_date = fields.Date(
        string="Provider rate date",
        readonly=True,
        copy=False,
        help="As-of date returned by the FX provider (may differ from the Odoo rate date).",
    )
    travel_last_fetch_datetime = fields.Datetime(
        string="Last travel fetch",
        readonly=True,
        copy=False,
    )

    def _travel_effective_sell_margin_percent(self):
        """Margin for sell = buy × (1+m): line if positive, else currency, else ICP, else fallback."""
        self.ensure_one()
        m = self.travel_sell_margin_percent or 0.0
        if m > 0:
            return m
        m = self.currency_id.travel_sell_margin_percent or 0.0
        if m > 0:
            return m
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("custom_currency_rates.travel_default_sell_margin_percent", "0")
        )
        try:
            m = float(raw)
        except (TypeError, ValueError):
            m = 0.0
        if m > 0:
            return m
        if (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("custom_currency_rates.travel_disable_sell_margin_fallback", "")
            .strip()
            .lower()
            in ("1", "true", "yes")
        ):
            return 0.0
        return TRAVEL_SELL_MARGIN_FALLBACK_PERCENT

    def _sync_travel_sell_from_buy(self):
        """Keep sell = buy × (1+m) for non-Frankfurter rows using effective margin (never 0% unless disabled)."""
        ctx = {SKIP_TRAVEL_SELL_SYNC_CTX: True}
        for rec in self:
            if rec.travel_provider:
                continue
            if not rec.company_rate:
                continue
            m_eff = rec._travel_effective_sell_margin_percent()
            sell = rec.company_rate * (1.0 + m_eff / 100.0)
            line_m = rec.travel_sell_margin_percent or 0.0
            vals = {}
            if float_compare(rec.travel_sell_rate or 0.0, sell, precision_digits=12) != 0:
                vals["travel_sell_rate"] = sell
            if line_m <= 0 < m_eff:
                vals["travel_sell_margin_percent"] = m_eff
            if vals:
                rec.with_context(**ctx).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        Currency = self.env["res.currency"]
        for vals in vals_list:
            if "travel_sell_margin_percent" not in vals and vals.get("currency_id"):
                curr = Currency.browse(vals["currency_id"])
                vals["travel_sell_margin_percent"] = curr.travel_sell_margin_percent
        records = super().create(vals_list)
        if not self.env.context.get(SKIP_TRAVEL_SELL_SYNC_CTX):
            records.filtered(lambda r: not r.travel_provider)._sync_travel_sell_from_buy()
        return records

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get(SKIP_TRAVEL_SELL_SYNC_CTX):
            return res
        touch_buy = {"company_rate", "travel_sell_margin_percent"} & vals.keys()
        if touch_buy and "travel_sell_rate" not in vals:
            self.filtered(lambda r: not r.travel_provider)._sync_travel_sell_from_buy()
        return res
