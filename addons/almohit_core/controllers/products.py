import math

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
    saleable_brands,
    safe_serialize_template,
    serialize_brand,
    serialize_category,
    serialize_template,
    template_in_stock,
)
from ..services.currencies import convert_price_map, resolve_display_currency

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


def _parse_moq_bound(raw, name):
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    if raw in (None, ""):
        return None, None
    text = str(raw).strip()
    if text == "":
        return None, None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None, error(
            "INVALID_FILTER",
            "%s must be a number >= 0." % name,
            400,
        )
    if not math.isfinite(value) or value < 0:
        return None, error(
            "INVALID_FILTER",
            "%s must be a number >= 0." % name,
            400,
        )
    return value, None


def _template_sort_prices(user, templates):
    if not templates:
        return {}
    pricelist = user.partner_id.sudo().property_product_pricelist
    if not pricelist:
        return {}
    return pricelist.sudo()._get_products_price(templates, 1.0) or {}


def _pricelist_currency(user):
    pricelist = user.partner_id.sudo().property_product_pricelist
    if not pricelist:
        return user.env.company.sudo().currency_id
    return pricelist.sudo().currency_id or user.env.company.sudo().currency_id


def _parse_display_currency(env, kwargs):
    currency = resolve_display_currency(env, kwargs.get("currency"))
    if currency is False:
        return None, error(
            "INVALID_CURRENCY",
            "currency must be an active currency code (e.g. SAR, USD).",
            400,
        )
    return currency, None


def _priced_for_display(env, user, template_prices, variant_prices, display_currency):
    if not display_currency:
        return template_prices, variant_prices, None
    source = _pricelist_currency(user)
    return (
        convert_price_map(env, template_prices, source, display_currency),
        convert_price_map(env, variant_prices, source, display_currency),
        display_currency.name,
    )


def _finite_price(raw):
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount):
        return None
    return amount


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

        display_currency, currency_err = _parse_display_currency(request.env, kwargs)
        if currency_err:
            return currency_err

        raw_category_id = kwargs.get("category_id")
        raw_brand_id = kwargs.get("brand_id")
        raw_in_stock = kwargs.get("in_stock")
        raw_include_children = kwargs.get("include_children")
        category_id = None
        brand_id = None
        in_stock = None
        include_children = False
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
        if raw_include_children not in (None, ""):
            include_children_value = str(raw_include_children).lower()
            if include_children_value not in ("true", "false"):
                return error(
                    "INVALID_FILTER",
                    "include_children must be true or false.",
                    400,
                )
            include_children = include_children_value == "true"
            if category_id is None:
                return error(
                    "INVALID_FILTER",
                    "include_children requires category_id.",
                    400,
                )

        min_moq, min_err = _parse_moq_bound(kwargs.get("min_moq"), "min_moq")
        if min_err:
            return min_err
        max_moq, max_err = _parse_moq_bound(kwargs.get("max_moq"), "max_moq")
        if max_err:
            return max_err
        if min_moq is not None and max_moq is not None and min_moq > max_moq:
            return error(
                "INVALID_FILTER",
                "min_moq must be less than or equal to max_moq.",
                400,
            )
        max_price, max_price_err = _parse_moq_bound(kwargs.get("max_price"), "max_price")
        if max_price_err:
            return max_price_err

        raw_sort = kwargs.get("sort")
        order = "name, id"
        price_sort = None
        if raw_sort not in (None, ""):
            if raw_sort == "name_asc":
                order = "name asc, id asc"
            elif raw_sort == "name_desc":
                order = "name desc, id desc"
            elif raw_sort == "newest":
                order = "create_date desc, id desc"
            elif raw_sort == "oldest":
                order = "create_date asc, id asc"
            elif raw_sort in ("price_asc", "price_desc"):
                price_sort = raw_sort
            else:
                return error(
                    "INVALID_SORT",
                    "sort must be one of: name_asc, name_desc, newest, oldest, price_asc, price_desc.",
                    400,
                )

        user = request.env.user
        if (price_sort or max_price is not None) and not may_see_price(user):
            return error(
                "WHOLESALE_APPROVAL_REQUIRED",
                "Wholesale approval is required to filter or sort by price.",
                403,
            )

        raw_q = kwargs.get("q")
        if isinstance(raw_q, (list, tuple)):
            raw_q = raw_q[0] if raw_q else ""
        q = str(raw_q).strip() if raw_q not in (None, "") else ""

        templates_model = request.env["product.template"].sudo()
        domain = list(SALEABLE_DOMAIN)
        if category_id is not None:
            if include_children:
                domain.append(("categ_id", "child_of", category_id))
            else:
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
        if min_moq is not None and min_moq > 0:
            domain.append(("almohit_min_order_qty", ">=", min_moq))
        if max_moq is not None:
            # Unset MOQ (False) must still match an upper bound filter.
            domain += [
                "|",
                ("almohit_min_order_qty", "in", (False, 0)),
                ("almohit_min_order_qty", "<=", max_moq),
            ]
        if price_sort or max_price is not None:
            templates = templates_model.search(domain, order=order)
            if in_stock is not None:
                warehouse = company_warehouse(request.env)
                templates = templates.filtered(
                    lambda tmpl: template_in_stock(tmpl, warehouse) == in_stock
                )
            priced = _template_sort_prices(user, templates)
            if display_currency:
                priced = convert_price_map(
                    request.env,
                    priced,
                    _pricelist_currency(user),
                    display_currency,
                )
            if max_price is not None:
                def within_max_price(tmpl):
                    amount = _finite_price(priced.get(tmpl.id))
                    return amount is not None and amount <= max_price

                templates = templates.filtered(within_max_price)
            if price_sort:
                reverse = price_sort == "price_desc"

                def price_sort_key(tmpl):
                    amount = _finite_price(priced.get(tmpl.id))
                    if amount is None:
                        return (1, 0, tmpl.id)
                    return (0, -amount if reverse else amount, tmpl.id)

                ordered = sorted(templates, key=price_sort_key)
            else:
                ordered = list(templates)
            total = len(ordered)
            offset = (page - 1) * page_size
            templates = templates_model.browse(
                [tmpl.id for tmpl in ordered[offset:offset + page_size]]
            )
        elif in_stock is None:
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
        template_prices, variant_prices, currency_name = _priced_for_display(
            request.env, user, template_prices, variant_prices, display_currency,
        )
        pages = 0 if total == 0 else (total + page_size - 1) // page_size
        items = []
        for tmpl in templates:
            serialized = safe_serialize_template(
                tmpl,
                include_price=include_price,
                template_prices=template_prices,
                variant_prices=variant_prices,
                currency_name=currency_name,
            )
            if not serialized:
                continue
            items.append(_allowlisted_list_product(serialized))
        return success({
            "items": items,
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
        display_currency, currency_err = _parse_display_currency(request.env, kwargs)
        if currency_err:
            return currency_err

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
        template_prices, variant_prices, currency_name = _priced_for_display(
            request.env, user, template_prices, variant_prices, display_currency,
        )
        serialized = safe_serialize_template(
            tmpl,
            include_price=include_price,
            template_prices=template_prices,
            variant_prices=variant_prices,
            detail=True,
            currency_name=currency_name,
        )
        if not serialized:
            return error(
                "PRODUCT_NOT_FOUND",
                "Product not found.",
                404,
            )
        return success(serialized)

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

    @http.route(
        "/almohit/api/brands",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def list_brands(self, **kwargs):
        return success([
            serialize_brand(brand)
            for brand in saleable_brands(request.env)
        ])
