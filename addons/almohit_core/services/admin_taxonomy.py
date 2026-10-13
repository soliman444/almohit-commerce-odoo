from .admin_products import (
    _as_int,
    _decode_image,
    serialize_admin_attribute,
    serialize_admin_brand,
    serialize_admin_category,
)
from .api import error
from .catalog import image_url


def _trim_name(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def serialize_created_category(category):
    parent = category.parent_id
    return {
        "id": category.id,
        "name": category.name,
        "complete_name": category.complete_name or category.name,
        "parent": {"id": parent.id, "name": parent.name} if parent else None,
    }


def create_admin_category(env, payload):
    name = _trim_name(payload.get("name"))
    if not name:
        return None, error("NAME_REQUIRED", "Category name is required.", 400)

    parent = None
    if payload.get("parent_id") not in (None, "", False):
        parent_id = _as_int(payload.get("parent_id"))
        if not parent_id:
            return None, error("CATEGORY_NOT_FOUND", "Parent category not found.", 400)
        parent = env["product.category"].browse(parent_id).exists()
        if not parent:
            return None, error("CATEGORY_NOT_FOUND", "Parent category not found.", 400)

    domain = [("name", "=ilike", name)]
    if parent:
        domain.append(("parent_id", "=", parent.id))
    else:
        domain.append(("parent_id", "=", False))
    if env["product.category"].search(domain, limit=1):
        return None, error(
            "CATEGORY_EXISTS",
            "A category with this name already exists under that parent.",
            409,
        )

    category = env["product.category"].create({
        "name": name,
        "parent_id": parent.id if parent else False,
    })
    return serialize_created_category(category), None


def create_admin_brand(env, payload):
    name = _trim_name(payload.get("name"))
    if not name:
        return None, error("NAME_REQUIRED", "Brand name is required.", 400)

    existing = env["almohit.product.brand"].with_context(active_test=False).search(
        [("name", "=ilike", name)],
        limit=1,
    )
    if existing:
        return None, error("BRAND_EXISTS", "A brand with this name already exists.", 409)

    image_b64, image_err = _decode_image(payload)
    if image_err:
        return None, image_err

    vals = {
        "name": name,
        "active": True,
    }
    if image_b64:
        vals["image_1920"] = image_b64
    brand = env["almohit.product.brand"].create(vals)
    return serialize_admin_brand(brand), None


def _parse_value_names(payload, required=True):
    raw_values = payload.get("values")
    if raw_values in (None, ""):
        raw_values = []
        single = _trim_name(payload.get("name") if "values" not in payload else "")
        if not single and payload.get("value"):
            single = _trim_name(payload.get("value") if isinstance(payload.get("value"), str) else "")
        if single:
            raw_values = [single]
    if not isinstance(raw_values, list):
        return None, error("INVALID_ATTRIBUTE_VALUES", "Attribute values are invalid.", 400)
    names = []
    seen = set()
    for raw in raw_values:
        if isinstance(raw, str):
            value_name = _trim_name(raw)
        elif isinstance(raw, dict):
            value_name = _trim_name(raw.get("name"))
        else:
            value_name = ""
        if not value_name:
            return None, error("INVALID_ATTRIBUTE_VALUES", "Attribute value is invalid.", 400)
        key = value_name.casefold()
        if key in seen:
            return None, error(
                "ATTRIBUTE_VALUE_EXISTS",
                "Duplicate values are not allowed on the same attribute.",
                409,
            )
        seen.add(key)
        names.append(value_name)
    if required and not names:
        return None, error("INVALID_ATTRIBUTE_VALUES", "Add at least one attribute value.", 400)
    return names, None


def create_admin_attribute(env, payload):
    name = _trim_name(payload.get("name"))
    if not name:
        return None, error("NAME_REQUIRED", "Attribute name is required.", 400)
    if env["product.attribute"].search([("name", "=ilike", name)], limit=1):
        return None, error("ATTRIBUTE_EXISTS", "An attribute with this name already exists.", 409)

    values, err = _parse_value_names(payload, required=True)
    if err:
        return None, err

    attribute = env["product.attribute"].create({
        "name": name,
        "create_variant": "always",
        "value_ids": [(0, 0, {"name": value_name}) for value_name in values],
    })
    return serialize_admin_attribute(attribute), None


def add_admin_attribute_values(env, attribute_id, payload):
    attribute = env["product.attribute"].browse(attribute_id).exists()
    if not attribute:
        return None, error("ATTRIBUTE_NOT_FOUND", "Attribute not found.", 400)
    if attribute.create_variant != "always":
        return None, error(
            "ATTRIBUTE_NOT_FOUND",
            "Only instantly-created variant attributes can be used on the storefront.",
            400,
        )

    names, err = _parse_value_names(payload, required=True)
    if err:
        return None, err

    existing = {value.name.casefold() for value in attribute.value_ids}
    for value_name in names:
        if value_name.casefold() in existing:
            return None, error(
                "ATTRIBUTE_VALUE_EXISTS",
                "That value already exists on this attribute.",
                409,
            )

    env["product.attribute.value"].create([
        {"attribute_id": attribute.id, "name": value_name}
        for value_name in names
    ])
    return serialize_admin_attribute(attribute), None


def _template_counts(env, field, ids):
    counts = {}
    if not ids:
        return counts
    grouped = env["product.template"].read_group(
        [(field, "in", ids)],
        [field],
        [field],
    )
    for row in grouped:
        rel = row.get(field)
        if rel:
            counts[rel[0]] = row.get("%s_count" % field) or 0
    return counts


def list_admin_categories(env):
    categories = env["product.category"].search([], order="complete_name, id")
    counts = _template_counts(env, "categ_id", categories.ids)
    rows = []
    for category in categories:
        item = serialize_admin_category(category)
        item["parent_name"] = category.parent_id.name or None
        item["product_count"] = counts.get(category.id, 0)
        rows.append(item)
    return rows


def list_admin_brands(env):
    brands = env["almohit.product.brand"].with_context(active_test=False).search(
        [],
        order="name, id",
    )
    counts = _template_counts(env, "almohit_brand_id", brands.ids)
    rows = []
    for brand in brands:
        item = serialize_admin_brand(brand)
        item["product_count"] = counts.get(brand.id, 0)
        item["image_url"] = image_url(brand, "image_256")
        rows.append(item)
    return rows


def list_admin_attributes(env):
    attributes = env["product.attribute"].search(
        [("create_variant", "=", "always")],
        order="sequence, name, id",
    )
    return [serialize_admin_attribute(attribute) for attribute in attributes]


def catalog_stats(env):
    return {
        "products": env["product.template"].search_count([]),
        "categories": env["product.category"].search_count([]),
        "brands": env["almohit.product.brand"].with_context(active_test=False).search_count([]),
        "attributes": env["product.attribute"].search_count([("create_variant", "=", "always")]),
    }
