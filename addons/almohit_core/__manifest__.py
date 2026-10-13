{
    "name": "Almohit Core",
    "version": "17.0.1.41.0",
    "summary": "Core backend and API for Almohit",
    "category": "Sales",
    "author": "Almohit",
    "license": "LGPL-3",

    "depends": [
        "base",
        "contacts",
        "product",
        "sale",
        "sale_stock",
        "account",
        "account_payment",
    ],

    "post_init_hook": "post_init_hook",

    "data": [
        "security/ir.model.access.csv",
        "data/product_pricelist_data.xml",
        "data/whatsapp_cron.xml",
        "data/whatsapp_parameters.xml",
        "data/notification_template_data.xml",
        "views/trade_application_views.xml",
        "views/notification_template_views.xml",
        "views/res_config_settings_views.xml",
        "views/product_brand_views.xml",
        "views/product_template_views.xml",
        "views/sale_order_views.xml",
        "views/stock_picking_views.xml",
    ],

    "installable": True,
    "application": False,
}
