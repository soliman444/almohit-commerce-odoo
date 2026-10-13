import base64
import binascii
import itertools
import re

from .api import error
from .catalog import image_url
from .stock import company_warehouse, is_inventory_managed


WHOLESALE_XMLID = "almohit_core.pricelist_wholesale"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}
ADMIN_LIST_LIMIT = 200
MAX_GALLERY_IMAGES = 12


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def serialize_admin_category(category):
    return {
        "id": category.id,
        "name": category.name,
        "complete_name": category.complete_name or category.name,
        "parent_id": category.parent_id.id or None,
    }


def serialize_admin_brand(brand):
    return {
        "id": brand.id,
        "name": brand.name,
        "active": bool(brand.active),
    }


def serialize_admin_attribute(attribute):
    values = attribute.value_ids.sorted(key=lambda row: (row.sequence, row.id))
    return {
        "id": attribute.id,
        "name": attribute.name,
        "create_variant": attribute.create_variant,
        "values": [{"id": value.id, "name": value.name} for value in values],
    }


def list_admin_product_options(env):
    categories = env["product.category"].search([], order="complete_name, id")
    brands = env["almohit.product.brand"].search(
        [("active", "=", True)],
        order="name, id",
    )
    attributes = env["product.attribute"].search(
        [("create_variant", "=", "always")],
        order="sequence, name, id",
    )
    return {
        "categories": [serialize_admin_category(row) for row in categories],
        "brands": [serialize_admin_brand(row) for row in brands],
        "attributes": [serialize_admin_attribute(row) for row in attributes],
    }


def _admin_status(template):
    if not template.active:
        return "ARCHIVED"
    if template.sale_ok:
        return "ACTIVE"
    return "INACTIVE"


def _is_variable(template):
    return bool(template.has_configurable_attributes or template.product_variant_count > 1)


def _template_qty(template, warehouse):
    product = template
    if warehouse:
        product = template.with_context(warehouse=warehouse.id)
    return float(product.qty_available or 0.0)


def _wholesale_pricelist(env):
    return env.ref(WHOLESALE_XMLID, raise_if_not_found=False)


def _wholesale_amounts(pricelist, records):
    if not pricelist or not records:
        return {}
    return pricelist._get_products_price(records, 1.0) or {}


def _product_cost(record):
    return float(record.standard_price or 0.0)


def serialize_admin_product(template, wholesale_price=None, warehouse=None, detail=False):
    variants = template.product_variant_ids.with_context(active_test=False)
    active_variants = variants.filtered("active")
    sku = template.default_code or ""
    if not sku and len(active_variants) == 1:
        sku = active_variants[:1].default_code or ""
    costs = [_product_cost(variant) for variant in active_variants]
    item = {
        "id": template.id,
        "name": template.name,
        "sku": sku or "",
        "active": bool(template.active),
        "sale_ok": bool(template.sale_ok),
        "status": _admin_status(template),
        "product_type": "VARIABLE" if _is_variable(template) else "SIMPLE",
        "detailed_type": template.detailed_type,
        "invoice_policy": template.invoice_policy,
        "min_order_qty": template.almohit_min_order_qty,
        "variant_count": len(active_variants),
        "qty_available": _template_qty(template, warehouse),
        "in_stock": bool(_template_qty(template, warehouse) > 0) if is_inventory_managed(template) else True,
        "wholesale_price": wholesale_price,
        "cost": _product_cost(template),
        "cost_min": min(costs) if costs else None,
        "cost_max": max(costs) if costs else None,
        "image_url": image_url(template, "image_256"),
        "category": serialize_admin_category(template.categ_id) if template.categ_id else None,
        "brand": serialize_admin_brand(template.almohit_brand_id) if template.almohit_brand_id else None,
    }
    if detail:
        variant_prices = _wholesale_amounts(_wholesale_pricelist(template.env), active_variants)
        item["description"] = template.description_sale or None
        item["barcode"] = template.barcode or None
        item["gallery"] = []
        if "product_template_image_ids" in template._fields:
            extras = template.product_template_image_ids.sorted(key=lambda row: (row.sequence, row.id))
            item["gallery"] = [image_url(extra, "image_256") for extra in extras if extra.image_256]
        item["variants"] = [
            serialize_admin_variant(variant, warehouse, variant_prices.get(variant.id))
            for variant in active_variants
        ]
    return item


def serialize_admin_variant(variant, warehouse=None, wholesale_price=None):
    qty_record = variant.with_context(warehouse=warehouse.id) if warehouse else variant
    return {
        "id": variant.id,
        "sku": variant.default_code or "",
        "barcode": variant.barcode or None,
        "active": bool(variant.active),
        "qty_available": float(qty_record.qty_available or 0.0),
        "wholesale_price": wholesale_price,
        "cost": _product_cost(variant),
        "image_url": image_url(variant, "image_variant_256"),
        "value_ids": variant.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids,
        "attributes": [
            {
                "attribute_id": ptav.attribute_id.id,
                "attribute": ptav.attribute_id.name,
                "value_id": ptav.product_attribute_value_id.id,
                "value": ptav.name,
            }
            for ptav in variant.product_template_attribute_value_ids
        ],
    }


def serialize_created_product(template):
    variants = template.product_variant_ids.filtered("active")
    variant = variants[:1]
    return {
        "id": template.id,
        "variant_id": variant.id if variant else None,
        "name": template.name,
        "sku": (template.default_code or (variant.default_code if variant else "") or ""),
        "active": bool(template.active and template.sale_ok),
        "variant_count": len(variants),
        "product_type": "VARIABLE" if _is_variable(template) else "SIMPLE",
    }


def list_admin_products(env, params=None):
    params = params or {}
    Template = env["product.template"].with_context(active_test=False)
    domain = []
    q = (params.get("q") or "").strip()
    if q:
        domain += [
            "|",
            "|",
            ("name", "ilike", q),
            ("default_code", "ilike", q),
            ("product_variant_ids.default_code", "ilike", q),
        ]
    category_id = _as_int(params.get("category_id"))
    if category_id:
        domain.append(("categ_id", "=", category_id))
    status = (params.get("status") or "").strip().upper()
    if status == "ACTIVE":
        domain += [("active", "=", True), ("sale_ok", "=", True)]
    elif status == "INACTIVE":
        domain += [("active", "=", True), ("sale_ok", "=", False)]
    elif status == "ARCHIVED":
        domain += [("active", "=", False)]
    product_type = (params.get("type") or "").strip().upper()
    if product_type == "VARIABLE":
        domain += [
            "|",
            ("has_configurable_attributes", "=", True),
            ("product_variant_count", ">", 1),
        ]
    elif product_type == "SIMPLE":
        domain += [
            ("has_configurable_attributes", "=", False),
            ("product_variant_count", "<=", 1),
        ]
    templates = Template.search(domain, order="id desc", limit=ADMIN_LIST_LIMIT)
    warehouse = company_warehouse(env)
    wholesale = _wholesale_pricelist(env)
    prices = _wholesale_amounts(wholesale, templates)
    return [
        serialize_admin_product(template, prices.get(template.id), warehouse)
        for template in templates
    ]


def get_admin_product(env, product_id):
    template = env["product.template"].with_context(active_test=False).browse(product_id).exists()
    if not template:
        return None, error("PRODUCT_NOT_FOUND", "Product not found.", 404)
    warehouse = company_warehouse(env)
    wholesale = _wholesale_pricelist(env)
    prices = _wholesale_amounts(wholesale, template)
    return serialize_admin_product(template, prices.get(template.id), warehouse, detail=True), None


def _sku_taken(env, sku, exclude_product_ids=None):
    sku = (sku or "").strip()
    if not sku:
        return False
    Product = env["product.product"].with_context(active_test=False)
    Template = env["product.template"].with_context(active_test=False)
    product_domain = [("default_code", "=", sku)]
    template_domain = [("default_code", "=", sku)]
    if exclude_product_ids:
        product_domain.append(("id", "not in", list(exclude_product_ids)))
    if Product.search(product_domain, limit=1):
        return True
    return bool(Template.search(template_domain, limit=1))


def _decode_image(payload):
    raw = payload.get("image_base64")
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, str):
        return None, error("INVALID_IMAGE", "Image data is invalid.", 400)
    content_type = (payload.get("image_content_type") or "").strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        return None, error("INVALID_IMAGE", "Image type is not allowed.", 400)
    cleaned = re.sub(r"^data:[^;]+;base64,", "", raw).replace("\n", "")
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        return None, error("INVALID_IMAGE", "Image data is invalid.", 400)
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None, error("INVALID_IMAGE", "Image is missing or too large.", 400)
    signatures = ALLOWED_IMAGE_TYPES[content_type]
    if content_type == "image/webp":
        if not (data.startswith(b"RIFF") and b"WEBP" in data[:16]):
            return None, error("INVALID_IMAGE", "Image data is invalid.", 400)
    elif not any(data.startswith(sig) for sig in signatures):
        return None, error("INVALID_IMAGE", "Image data is invalid.", 400)
    return cleaned, None


def _decode_gallery(payload):
    raw = payload.get("gallery")
    if raw in (None, ""):
        return [], None
    if not isinstance(raw, list):
        return None, error("INVALID_IMAGE", "Gallery images are invalid.", 400)
    if len(raw) > MAX_GALLERY_IMAGES:
        return None, error("INVALID_IMAGE", "Too many gallery images.", 400)
    images = []
    for item in raw:
        if not isinstance(item, dict):
            return None, error("INVALID_IMAGE", "Gallery images are invalid.", 400)
        data, err = _decode_image(item)
        if err:
            return None, err
        if data:
            images.append(data)
    return images, None


def _set_gallery(env, template, images):
    Image = env["product.image"]
    for index, data in enumerate(images):
        Image.create({
            "name": "%s (%s)" % (template.name, index + 1),
            "sequence": (index + 1) * 10,
            "product_tmpl_id": template.id,
            "image_1920": data,
        })


def _set_wholesale_price(env, template, amount):
    wholesale = _wholesale_pricelist(env)
    if not wholesale:
        return error(
            "INVALID_PRICE",
            "The Almohit Wholesale pricelist is not configured.",
            400,
        )
    existing = env["product.pricelist.item"].search([
        ("pricelist_id", "=", wholesale.id),
        ("product_tmpl_id", "=", template.id),
        ("applied_on", "=", "1_product"),
        ("product_id", "=", False),
    ], limit=1)
    vals = {
        "pricelist_id": wholesale.id,
        "compute_price": "fixed",
        "fixed_price": amount,
        "min_quantity": 0.0,
        "applied_on": "1_product",
        "product_tmpl_id": template.id,
        "product_id": False,
        "currency_id": wholesale.currency_id.id,
    }
    if existing:
        existing.write({
            "fixed_price": amount,
            "compute_price": "fixed",
        })
    else:
        env["product.pricelist.item"].create(vals)
    if "list_price" in template._fields:
        template.list_price = amount
    return None


def _set_cost(product, amount):
    product.standard_price = amount


def _parse_cost(payload, key="cost"):
    if payload.get(key) in (None, ""):
        return None, None
    amount = _as_float(payload.get(key))
    if amount is None or amount < 0:
        return None, error("INVALID_COST", "Cost is invalid.", 400)
    return amount, None


def _set_variant_wholesale_price(env, variant, amount):
    wholesale = _wholesale_pricelist(env)
    if not wholesale:
        return error(
            "INVALID_PRICE",
            "The Almohit Wholesale pricelist is not configured.",
            400,
        )
    existing = env["product.pricelist.item"].search([
        ("pricelist_id", "=", wholesale.id),
        ("product_id", "=", variant.id),
        ("applied_on", "=", "0_product_variant"),
    ], limit=1)
    vals = {
        "pricelist_id": wholesale.id,
        "compute_price": "fixed",
        "fixed_price": amount,
        "min_quantity": 0.0,
        "applied_on": "0_product_variant",
        "product_tmpl_id": variant.product_tmpl_id.id,
        "product_id": variant.id,
        "currency_id": wholesale.currency_id.id,
    }
    if existing:
        existing.write({
            "fixed_price": amount,
            "compute_price": "fixed",
        })
    else:
        env["product.pricelist.item"].create(vals)
    return None


def _set_initial_quantity(env, product, qty):
    if not is_inventory_managed(product):
        return error(
            "INVALID_STOCK_QUANTITY",
            "Stock quantity is only for storable products.",
            400,
        )
    warehouse = company_warehouse(env)
    if not warehouse or not warehouse.lot_stock_id:
        return error(
            "INVALID_STOCK_QUANTITY",
            "No company warehouse is configured.",
            400,
        )
    Quant = env["stock.quant"].sudo().with_context(
        inventory_mode=True,
        inventory_ignore_warnings=True,
    )
    quant = Quant.search([
        ("product_id", "=", product.id),
        ("location_id", "=", warehouse.lot_stock_id.id),
    ], limit=1)
    if not quant:
        quant = Quant.create({
            "product_id": product.id,
            "location_id": warehouse.lot_stock_id.id,
            "inventory_quantity": qty,
        })
    else:
        quant.inventory_quantity = qty
    quant.action_apply_inventory()
    return None


def _common_template_vals(payload, category, brand, moq, sale_ok, sku, barcode, description, wholesale_price):
    vals = {
        "name": (payload.get("name") or "").strip(),
        "default_code": sku or False,
        "barcode": barcode,
        "description_sale": description,
        "categ_id": category.id,
        "almohit_brand_id": brand.id if brand else False,
        "almohit_min_order_qty": moq,
        "sale_ok": sale_ok,
        "active": True,
        "detailed_type": "product",
        "type": "product",
        "invoice_policy": "delivery",
    }
    if wholesale_price is not None:
        vals["list_price"] = wholesale_price
    return vals


def _parse_shared_create(env, payload, require_sku=True):
    name = (payload.get("name") or "").strip()
    if not name:
        return None, error("NAME_REQUIRED", "Product name is required.", 400)

    sku = (payload.get("sku") or "").strip()
    if require_sku and not sku:
        return None, error("SKU_ALREADY_EXISTS", "SKU is required.", 400)
    if sku and _sku_taken(env, sku):
        return None, error("SKU_ALREADY_EXISTS", "This SKU is already in use.", 409)

    category_id = _as_int(payload.get("category_id"))
    if not category_id:
        return None, error("CATEGORY_NOT_FOUND", "Category is required.", 400)
    category = env["product.category"].browse(category_id).exists()
    if not category:
        return None, error("CATEGORY_NOT_FOUND", "Category not found.", 400)

    brand = None
    if payload.get("brand_id") not in (None, "", False):
        brand_id = _as_int(payload.get("brand_id"))
        if not brand_id:
            return None, error("BRAND_NOT_FOUND", "Brand not found.", 400)
        brand = env["almohit.product.brand"].browse(brand_id).exists()
        if not brand or not brand.active:
            return None, error("BRAND_NOT_FOUND", "Brand not found.", 400)

    moq = _as_float(payload.get("min_order_qty") if "min_order_qty" in payload else 1)
    if moq is None or moq <= 0:
        return None, error("INVALID_MOQ", "Minimum order quantity must be greater than zero.", 400)

    product_type = (payload.get("type") or "product")
    if isinstance(product_type, str):
        product_type = product_type.strip()
    if product_type != "product":
        return None, error(
            "INVALID_STOCK_QUANTITY",
            "Only storable products can be created from the website in this version.",
            400,
        )

    sale_ok = payload.get("sale_ok", True)
    if not isinstance(sale_ok, bool):
        sale_ok = str(sale_ok).lower() in ("1", "true", "yes", "on")

    wholesale_price = None
    if payload.get("wholesale_price") not in (None, ""):
        wholesale_price = _as_float(payload.get("wholesale_price"))
        if wholesale_price is None or wholesale_price < 0:
            return None, error("INVALID_PRICE", "Wholesale price is invalid.", 400)

    cost, cost_err = _parse_cost(payload)
    if cost_err:
        return None, cost_err

    image_b64, image_err = _decode_image(payload)
    if image_err:
        return None, image_err
    gallery, gallery_err = _decode_gallery(payload)
    if gallery_err:
        return None, gallery_err

    return {
        "name": name,
        "sku": sku,
        "category": category,
        "brand": brand,
        "moq": moq,
        "sale_ok": sale_ok,
        "wholesale_price": wholesale_price,
        "cost": cost,
        "barcode": (payload.get("barcode") or "").strip() or False,
        "description": (payload.get("description") or "").strip() or False,
        "image_b64": image_b64,
        "gallery": gallery,
    }, None


def _parse_attribute_lines(env, payload):
    raw_lines = payload.get("attribute_lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        return None, error(
            "INVALID_ATTRIBUTE_VALUES",
            "Select at least one existing attribute and value.",
            400,
        )
    lines = []
    seen_attributes = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            return None, error("INVALID_ATTRIBUTE_VALUES", "Attribute selection is invalid.", 400)
        attribute_id = _as_int(raw.get("attribute_id"))
        raw_values = raw.get("value_ids") or []
        if not attribute_id or not isinstance(raw_values, list) or not raw_values:
            return None, error("INVALID_ATTRIBUTE_VALUES", "Each attribute needs at least one value.", 400)
        if attribute_id in seen_attributes:
            return None, error("INVALID_ATTRIBUTE_VALUES", "Duplicate attributes are not allowed.", 400)
        attribute = env["product.attribute"].browse(attribute_id).exists()
        if not attribute or attribute.create_variant != "always":
            return None, error("ATTRIBUTE_NOT_FOUND", "Attribute not found.", 400)
        value_ids = []
        for raw_value in raw_values:
            value_id = _as_int(raw_value)
            if not value_id:
                return None, error("INVALID_ATTRIBUTE_VALUES", "Attribute value is invalid.", 400)
            value = env["product.attribute.value"].browse(value_id).exists()
            if not value or value.attribute_id.id != attribute.id:
                return None, error("INVALID_ATTRIBUTE_VALUES", "Attribute value not found.", 400)
            if value_id not in value_ids:
                value_ids.append(value_id)
        seen_attributes.add(attribute_id)
        lines.append({"attribute": attribute, "value_ids": value_ids})
    return lines, None


def _parse_variant_overrides(payload, expected_keys):
    raw_variants = payload.get("variants") or []
    if raw_variants in (None, ""):
        raw_variants = []
    if not isinstance(raw_variants, list):
        return None, error("INVALID_ATTRIBUTE_VALUES", "Variant payload is invalid.", 400)
    overrides = {}
    skus = []
    for raw in raw_variants:
        if not isinstance(raw, dict):
            return None, error("INVALID_ATTRIBUTE_VALUES", "Variant payload is invalid.", 400)
        raw_values = raw.get("value_ids") or []
        if not isinstance(raw_values, list):
            return None, error("INVALID_ATTRIBUTE_VALUES", "Variant combination is invalid.", 400)
        key = frozenset(_as_int(value) for value in raw_values)
        if None in key or key not in expected_keys:
            return None, error(
                "INVALID_ATTRIBUTE_VALUES",
                "Variant combination does not match the selected attributes.",
                400,
            )
        if key in overrides:
            return None, error("INVALID_ATTRIBUTE_VALUES", "Duplicate variant combination.", 400)
        sku = (raw.get("sku") or "").strip()
        barcode = (raw.get("barcode") or "").strip() or False
        wholesale_price = None
        if raw.get("wholesale_price") not in (None, ""):
            wholesale_price = _as_float(raw.get("wholesale_price"))
            if wholesale_price is None or wholesale_price < 0:
                return None, error("INVALID_PRICE", "Wholesale price is invalid.", 400)
        cost, cost_err = _parse_cost(raw)
        if cost_err:
            return None, cost_err
        qty = 0.0
        if raw.get("quantity") not in (None, ""):
            qty = _as_float(raw.get("quantity"))
            if qty is None or qty < 0:
                return None, error("INVALID_STOCK_QUANTITY", "Stock quantity is invalid.", 400)
        image_b64, image_err = _decode_image(raw)
        if image_err:
            return None, image_err
        if sku:
            skus.append(sku)
        overrides[key] = {
            "sku": sku,
            "barcode": barcode,
            "wholesale_price": wholesale_price,
            "cost": cost,
            "quantity": qty or 0.0,
            "image_b64": image_b64,
        }
    if len(skus) != len(set(skus)):
        return None, error("SKU_ALREADY_EXISTS", "This SKU is already in use.", 409)
    return overrides, None


def _combination_key(variant):
    return frozenset(
        variant.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids
    )


def create_simple_product(env, payload):
    parsed, err = _parse_shared_create(env, payload, require_sku=True)
    if err:
        return None, err

    qty = 0.0
    if payload.get("quantity") not in (None, ""):
        qty = _as_float(payload.get("quantity"))
        if qty is None or qty < 0:
            return None, error("INVALID_STOCK_QUANTITY", "Stock quantity is invalid.", 400)

    vals = _common_template_vals(
        payload,
        parsed["category"],
        parsed["brand"],
        parsed["moq"],
        parsed["sale_ok"],
        parsed["sku"],
        parsed["barcode"],
        parsed["description"],
        parsed["wholesale_price"],
    )
    template = env["product.template"].create(vals)
    try:
        variant = template.product_variant_ids[:1]
        if not variant:
            template.unlink()
            return None, error(
                "INVALID_STOCK_QUANTITY",
                "Native product variant was not created.",
                400,
            )
        if parsed["sku"] and variant.default_code != parsed["sku"]:
            variant.default_code = parsed["sku"]
        if parsed["barcode"]:
            variant.barcode = parsed["barcode"]
        if parsed["wholesale_price"] is not None:
            price_err = _set_wholesale_price(env, template, parsed["wholesale_price"])
            if price_err:
                template.unlink()
                return None, price_err
        if parsed["cost"] is not None:
            _set_cost(template, parsed["cost"])
        if parsed["image_b64"]:
            template.image_1920 = parsed["image_b64"]
        if parsed["gallery"]:
            _set_gallery(env, template, parsed["gallery"])
        if qty:
            stock_err = _set_initial_quantity(env, variant, qty)
            if stock_err:
                template.unlink()
                return None, stock_err
    except Exception:
        if template.exists():
            template.unlink()
        raise

    return serialize_created_product(template), None


def create_variable_product(env, payload):
    parsed, err = _parse_shared_create(env, payload, require_sku=False)
    if err:
        return None, err
    lines, err = _parse_attribute_lines(env, payload)
    if err:
        return None, err

    expected_keys = {
        frozenset(combo)
        for combo in itertools.product(*[line["value_ids"] for line in lines])
    }
    overrides, err = _parse_variant_overrides(payload, expected_keys)
    if err:
        return None, err
    for override in overrides.values():
        if override["sku"] and _sku_taken(env, override["sku"]):
            return None, error("SKU_ALREADY_EXISTS", "This SKU is already in use.", 409)
        if parsed["sku"] and override["sku"] and override["sku"] == parsed["sku"]:
            return None, error("SKU_ALREADY_EXISTS", "This SKU is already in use.", 409)

    vals = _common_template_vals(
        payload,
        parsed["category"],
        parsed["brand"],
        parsed["moq"],
        parsed["sale_ok"],
        parsed["sku"],
        parsed["barcode"],
        parsed["description"],
        parsed["wholesale_price"],
    )
    vals["attribute_line_ids"] = [
        (0, 0, {
            "attribute_id": line["attribute"].id,
            "value_ids": [(6, 0, line["value_ids"])],
        })
        for line in lines
    ]
    template = env["product.template"].create(vals)
    try:
        variants = template.product_variant_ids.filtered("active")
        generated = {_combination_key(variant): variant for variant in variants}
        if set(generated) != expected_keys:
            template.unlink()
            return None, error(
                "INVALID_ATTRIBUTE_VALUES",
                "Odoo did not generate the expected native variants.",
                400,
            )
        if parsed["wholesale_price"] is not None:
            price_err = _set_wholesale_price(env, template, parsed["wholesale_price"])
            if price_err:
                template.unlink()
                return None, price_err
        if parsed["cost"] is not None:
            _set_cost(template, parsed["cost"])
        if parsed["image_b64"]:
            template.image_1920 = parsed["image_b64"]
        if parsed["gallery"]:
            _set_gallery(env, template, parsed["gallery"])
        for key, variant in generated.items():
            override = overrides.get(key) or {}
            sku = override.get("sku")
            if sku:
                variant.default_code = sku
            elif parsed["sku"] and variant.default_code == parsed["sku"] and len(generated) > 1:
                variant.default_code = False
            barcode = override.get("barcode")
            if barcode:
                variant.barcode = barcode
            variant_price = override.get("wholesale_price")
            if variant_price is not None:
                price_err = _set_variant_wholesale_price(env, variant, variant_price)
                if price_err:
                    template.unlink()
                    return None, price_err
            if override.get("cost") is not None:
                _set_cost(variant, override["cost"])
            if override.get("image_b64"):
                variant.image_variant_1920 = override["image_b64"]
            qty = override.get("quantity") or 0.0
            if qty:
                stock_err = _set_initial_quantity(env, variant, qty)
                if stock_err:
                    template.unlink()
                    return None, stock_err
    except Exception:
        if template.exists():
            template.unlink()
        raise

    return serialize_created_product(template), None


def create_admin_product(env, payload):
    lines = payload.get("attribute_lines") or []
    if lines:
        return create_variable_product(env, payload)
    return create_simple_product(env, payload)
