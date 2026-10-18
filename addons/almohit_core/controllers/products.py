from odoo import http
from odoo.http import request

from ..services.api import success, error
from ..services.catalog import (
    SALEABLE_DOMAIN,
    company_warehouse,
    may_see_price,
    pricelist_prices,
    saleable_template,
    saleable_templates,
    serialize_category,
    serialize_template,
    template_in_stock,
)

_LIST_PRODUCT_KEYS = (
    "id",
    "name",
    "default_code",
    "type",
    "category",
    "brand",
    "image_url",
    "min_order_qty",
    "availability",
    "uom",
    "currency",
    "has_variants",
    "variants",
    "images",
    "price",
)
_LIST_CATEGORY_KEYS = ("id", "name", "complete_name", "parent_id")
_LIST_BRAND_KEYS = ("id", "name")
_LIST_UOM_KEYS = ("id", "name")
_LIST_AVAILABILITY_KEYS = ("in_stock", "qty_available")
_LIST_VARIANT_KEYS = (
    "id",
    "default_code",
    "barcode",
    "attributes",
    "image_url",
    "availability",
    "price",
)
_LIST_ATTRIBUTE_KEYS = ("attribute", "value")


def _allowlisted_mapping(payload, keys):
    if not isinstance(payload, dict):
        return payload
    return {key: payload[key] for key in keys if key in payload}


def _allowlisted_list_product(item):
    product = _allowlisted_mapping(item, _LIST_PRODUCT_KEYS)
    if "category" in product:
        product["category"] = _allowlisted_mapping(
            product["category"], _LIST_CATEGORY_KEYS
        )
    if "brand" in product:
        product["brand"] = _allowlisted_mapping(product["brand"], _LIST_BRAND_KEYS)
    if "uom" in product:
        product["uom"] = _allowlisted_mapping(product["uom"], _LIST_UOM_KEYS)
    if "availability" in product:
        product["availability"] = _allowlisted_mapping(
            product["availability"], _LIST_AVAILABILITY_KEYS
        )
    variants = product.get("variants")
    if isinstance(variants, list):
        allowed_variants = []
        for variant in variants:
            allowed = _allowlisted_mapping(variant, _LIST_VARIANT_KEYS)
            if "availability" in allowed:
                allowed["availability"] = _allowlisted_mapping(
                    allowed["availability"], _LIST_AVAILABILITY_KEYS
                )
            attributes = allowed.get("attributes")
            if isinstance(attributes, list):
                allowed["attributes"] = [
                    _allowlisted_mapping(attr, _LIST_ATTRIBUTE_KEYS)
                    for attr in attributes
                ]
            allowed_variants.append(allowed)
        product["variants"] = allowed_variants
    return product


class AlmohitProductsController(http.Controller):

    @http.route(
        "/almohit/api/products",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def list_products(self, **kwargs):
        try:
            page = 1 if kwargs.get("page") in (None, "") else int(kwargs.get("page"))
            page_size = 20 if kwargs.get("page_size") in (None, "") else int(kwargs.get("page_size"))
        except (TypeError, ValueError):
            return error(
                "INVALID_PAGINATION",
                "page must be >= 1 and page_size must be between 1 and 100.",
                400,
            )
        if page < 1 or page_size < 1 or page_size > 100:
            return error(
                "INVALID_PAGINATION",
                "page must be >= 1 and page_size must be between 1 and 100.",
                400,
            )

        raw_category_id = kwargs.get("category_id")
        raw_brand_id = kwargs.get("brand_id")
        raw_in_stock = kwargs.get("in_stock")
        category_id = None
        brand_id = None
        in_stock = None
        if raw_category_id not in (None, ""):
            try:
                category_id = int(raw_category_id)
            except (TypeError, ValueError):
                return error(
                    "INVALID_FILTER",
                    "category_id and brand_id must be positive integers.",
                    400,
                )
            if category_id < 1:
                return error(
                    "INVALID_FILTER",
                    "category_id and brand_id must be positive integers.",
                    400,
                )
        if raw_brand_id not in (None, ""):
            try:
                brand_id = int(raw_brand_id)
            except (TypeError, ValueError):
                return error(
                    "INVALID_FILTER",
                    "category_id and brand_id must be positive integers.",
                    400,
                )
            if brand_id < 1:
                return error(
                    "INVALID_FILTER",
                    "category_id and brand_id must be positive integers.",
                    400,
                )
        if raw_in_stock not in (None, ""):
            in_stock_value = str(raw_in_stock).lower()
            if in_stock_value not in ("true", "false"):
                return error(
                    "INVALID_FILTER",
                    "in_stock must be true or false.",
                    400,
                )
            in_stock = in_stock_value == "true"

        raw_sort = kwargs.get("sort")
        order = "name, id"
        if raw_sort not in (None, ""):
            if raw_sort == "name_asc":
                order = "name asc, id asc"
            elif raw_sort == "name_desc":
                order = "name desc, id desc"
            else:
                return error(
                    "INVALID_SORT",
                    "sort must be one of: name_asc, name_desc.",
                    400,
                )

        raw_q = kwargs.get("q")
        if isinstance(raw_q, (list, tuple)):
            raw_q = raw_q[0] if raw_q else ""
        q = str(raw_q).strip() if raw_q not in (None, "") else ""

        user = request.env.user
        templates_model = request.env["product.template"].sudo()
        domain = list(SALEABLE_DOMAIN)
        if category_id is not None:
            domain.append(("categ_id", "=", category_id))
        if brand_id is not None:
            domain.append(("almohit_brand_id", "=", brand_id))
            domain.append(("almohit_brand_id", "=", brand_id))
        if q:
            domain += [
                "|",
                "|",
                "|",
                "|",
                ("name", "ilike", q),
                ("default_code", "ilike", q),
                ("barcode", "ilike", q),
                ("product_variant_ids.default_code", "ilike", q),
                ("product_variant_ids.barcode", "ilike", q),
            ]
        if in_stock is None:
            total = templates_model.search_count(domain)
            templates = templates_model.search(
                domain,
                offset=(page - 1) * page_size,
                limit=page_size,
                order=order,
            )
        else:
            warehouse = company_warehouse(request.env)
            templates = templates_model.search(domain, order=order)
            templates = templates.filtered(
                lambda tmpl: template_in_stock(tmpl, warehouse) == in_stock
            )
            total = len(templates)
            offset = (page - 1) * page_size
            templates = templates[offset:offset + page_size]
        include_price = may_see_price(user)
        template_prices, variant_prices = pricelist_prices(user, templates)
        pages = 0 if total == 0 else (total + page_size - 1) // page_size
        return success({
            "items": [
                _allowlisted_list_product(
                    serialize_template(
                        tmpl,
                        include_price=include_price,
                        template_prices=template_prices,
                        variant_prices=variant_prices,
                    )
                )
                for tmpl in templates
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
                "has_next": page < pages,
                "has_previous": page > 1,
            },
        })

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
