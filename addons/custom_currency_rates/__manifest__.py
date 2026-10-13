# -*- coding: utf-8 -*-
{
    "name": "Custom Currency Rates (standalone)",
    "version": "17.0.1.5.0",
    "category": "Accounting",
    "summary": "Fetch FX rates via public API and update Odoo — no OCA dependency",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "security/custom_currency_rates_security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/ir_cron_travel.xml",
        "views/currency_rate_sync_views.xml",
        "views/res_currency_views.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
