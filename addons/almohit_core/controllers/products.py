from odoo import http
from odoo.http import request

from ..services.api import success, error
from ..services.catalog import (
    may_see_price,
    pricelist_prices,
    saleable_template,
    saleable_templates,
    serialize_category,
    serialize_template,
)


class AlmohitProductsController(http.Controller):

    @http.route(
        "/almohit/api/products",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def list_products(self, **kwargs):
        user = request.env.user
        templates = saleable_templates(request.env)
        include_price = may_see_price(user)
        template_prices, variant_prices = pricelist_prices(user, templates)
        return success([
            serialize_template(
                tmpl,
                include_price=include_price,
                template_prices=template_prices,
                variant_prices=variant_prices,
            )
            for tmpl in templates
        ])

    @http.route(
        "/almohit/api/products/<int:product_id>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def product_detail(self, product_id, **kwargs):
        user = request.env.user
        tmpl = saleable_template(request.env, product_id)
        if not tmpl:
            return error(
                "PRODUCT_NOT_FOUND",
                "Product not found.",
                404,
            )

        include_price = may_see_price(user)
        template_prices, variant_prices = pricelist_prices(user, tmpl)
        return success(
            serialize_template(
                tmpl,
                include_price=include_price,
                template_prices=template_prices,
                variant_prices=variant_prices,
                detail=True,
            )
        )

    @http.route(
        "/almohit/api/categories",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def list_categories(self, **kwargs):
        templates = saleable_templates(request.env)
        categories = templates.mapped("categ_id")
        categories = categories.sorted(
            key=lambda categ: (categ.complete_name or categ.name, categ.id)
        )
        return success([
            serialize_category(categ)
            for categ in categories
        ])
