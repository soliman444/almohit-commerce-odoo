import logging

from odoo.http import request
from odoo.tools import html2plaintext

from .stock import (
    availability_payload,
    available_qty,
    company_warehouse,
    product_in_stock,
    template_in_stock,
)

_logger = logging.getLogger(__name__)


SALEABLE_DOMAIN = [
    ("sale_ok", "=", True),
    ("active", "=", True),
]


def may_see_price(user):
    if not user or user._is_public():
        return False
    partner = user.partner_id
    if not partner:
        return False
    return partner.almohit_trade_status == "approved"


def saleable_templates(env):
    # Public/portal users may lack product.template ACL.
    # sudo() is scoped to active products that can be sold.
    return env["product.template"].sudo().search(
        SALEABLE_DOMAIN,
        order="name, id",
    )


def saleable_template(env, product_id):
    return env["product.template"].sudo().search(
        [("id", "=", product_id)] + SALEABLE_DOMAIN,
        limit=1,
    )


def pricelist_prices(user, templates, pricelist=None):
    if not may_see_price(user) or not templates:
        return {}, {}

    # Identity was already decided from request.env.user (no sudo).
    # sudo() only reads the server-approved storefront pricelist and runs
    # Odoo's pricelist engine for this partner.
    if pricelist is None:
        pricelist = user.partner_id.sudo().property_product_pricelist
    if not pricelist:
        return {}, {}

    template_prices = pricelist.sudo()._get_products_price(templates, 1.0)
    variants = templates.mapped("product_variant_ids").filtered("active")
    variant_prices = (
        pricelist.sudo()._get_products_price(variants, 1.0) if variants else {}
    )
    return template_prices, variant_prices


def image_url(record, field="image_256"):
    if not record or not record[field]:
        return None
    unique = "1"
    if record.write_date:
        unique = record.write_date.strftime("%Y%m%d%H%M%S")
    # Put the cache-buster in the path, not ?unique=, so Next.js Image
    # localPatterns can serve /odoo-media without a storefront config change.
    path = "/web/image/%s/%s/%s/%s" % (
        record._name,
        record.id,
        field,
        unique,
    )
    if request:
        return request.httprequest.host_url.rstrip("/") + path
    return path


def _variant_attributes(variant):
    rows = []
    for ptav in variant.product_template_attribute_value_ids:
        attribute = ptav.attribute_id
        if not attribute:
            continue
        rows.append({
            "attribute": attribute.name or "",
            "value": ptav.name or "",
        })
    return rows


def _serialize_variant(variant, include_price=False, variant_prices=None, warehouse=None):
    item = {
        "id": variant.id,
        "default_code": variant.default_code or None,
        "barcode": variant.barcode or None,
        "attributes": _variant_attributes(variant),
        "image_url": image_url(variant, "image_variant_256"),
        "availability": availability_payload(
            product_in_stock(variant, warehouse),
            available_qty(variant, warehouse) if include_price else None,
        ),
    }
    if include_price and variant_prices is not None and variant.id in variant_prices:
        item["price"] = variant_prices[variant.id]
    return item


def _template_has_variants(tmpl):
    try:
        if tmpl.has_configurable_attributes:
            return True
    except Exception:
        _logger.exception(
            "almohit: failed reading has_configurable_attributes for product.template %s",
            tmpl.id,
        )
    return bool(tmpl.product_variant_count > 1)


def _template_extra_images(tmpl):
    images = []
    if "product_template_image_ids" not in tmpl._fields:
        return images
    try:
        extras = tmpl.product_template_image_ids.sorted(
            key=lambda row: (row.sequence, row.id)
        )
    except Exception:
        _logger.exception(
            "almohit: failed reading extra images for product.template %s",
            tmpl.id,
        )
        return images
    for extra in extras:
        try:
            url = image_url(extra, "image_1024")
        except Exception:
            _logger.exception(
                "almohit: failed building image url for product.template %s image %s",
                tmpl.id,
                extra.id,
            )
            continue
        if url:
            images.append(url)
    return images


def serialize_template(
    tmpl,
    include_price=False,
    template_prices=None,
    variant_prices=None,
    detail=False,
    currency_name=None,
):
    variants = tmpl.product_variant_ids.filtered("active")
    warehouse = company_warehouse(tmpl.env)
    category = None
    if tmpl.categ_id:
        try:
            category = serialize_category(tmpl.categ_id)
        except Exception:
            _logger.exception(
                "almohit: failed serializing category for product.template %s",
                tmpl.id,
            )
            category = {
                "id": tmpl.categ_id.id,
                "name": tmpl.categ_id.name or "",
                "complete_name": tmpl.categ_id.name or "",
                "parent_id": None,
            }
    brand = None
    if tmpl.almohit_brand_id:
        brand = {
            "id": tmpl.almohit_brand_id.id,
            "name": tmpl.almohit_brand_id.name,
        }
    item = {
        "id": tmpl.id,
        "name": tmpl.name,
        "default_code": tmpl.default_code or None,
        "type": tmpl.type,
        "category": category,
        "brand": brand,
        "image_url": image_url(tmpl, "image_256"),
        "min_order_qty": float(tmpl.almohit_min_order_qty or 0.0),
        "availability": availability_payload(
            template_in_stock(tmpl, warehouse),
            (
                available_qty(variants[:1], warehouse)
                if include_price and len(variants) == 1
                else None
            ),
        ),
        "uom": (
            {"id": tmpl.uom_id.id, "name": tmpl.uom_id.name}
            if tmpl.uom_id
            else None
        ),
        "currency": currency_name or tmpl.env.company.sudo().currency_id.name,
        "has_variants": _template_has_variants(tmpl),
        "variants": [
            _serialize_variant(
                variant,
                include_price=include_price,
                variant_prices=variant_prices,
                warehouse=warehouse,
            )
            for variant in variants
        ],
    }

    item["images"] = _template_extra_images(tmpl)

    if include_price and template_prices is not None and tmpl.id in template_prices:
        item["price"] = template_prices[tmpl.id]

    if detail:
        item["description"] = (
            html2plaintext(tmpl.description_sale or "") or None
        )
        item["barcode"] = tmpl.barcode or None
        if brand and tmpl.almohit_brand_id:
            brand["image_url"] = image_url(tmpl.almohit_brand_id, "image_256")

    return item


def safe_serialize_template(tmpl, **kwargs):
    """Serialize one template; return None if this product is corrupt."""
    try:
        return serialize_template(tmpl, **kwargs)
    except Exception:
        _logger.exception(
            "almohit: skipped corrupt product.template id=%s name=%r",
            tmpl.id,
            tmpl.display_name,
        )
        return None


def serialize_category(category):
    return {
        "id": category.id,
        "name": category.name,
        "complete_name": category.complete_name,
        "parent_id": category.parent_id.id or None,
    }


def serialize_brand(brand):
    return {
        "id": brand.id,
        "name": brand.name,
    }


def saleable_brands(env):
    brands = saleable_templates(env).mapped("almohit_brand_id")
    return brands.sorted(key=lambda brand: (brand.name or "", brand.id))
