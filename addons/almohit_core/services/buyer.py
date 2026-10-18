import math
from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError
from .admin_products import PRODUCT_IMAGE_MAX_BYTES, _decode_image
from .api import (
    _query_scalar,
    error,
    paginated_success,
    parse_date_range_params,
    parse_pagination,
    success,
)
from .catalog import image_url, serialize_brand, serialize_category


SUPPLIER_FIELDS = {
    "name",
    "phone",
    "mobile",
    "email",
    "street",
    "street2",
    "city",
    "zip",
    "state_id",
    "country_id",
    "tax_number",
    "vat",
    "notes",
    "comment",
}
PRODUCT_CREATE_FIELDS = {
    "name",
    "default_code",
    "barcode",
    "category_id",
    "brand_id",
    "uom_id",
    "uom_po_id",
    "type",
    "purchase_description",
    "image_base64",
    "image_content_type",
}
PURCHASE_CREATE_FIELDS = {"supplier_id", "date_order", "notes", "lines"}
PURCHASE_PATCH_FIELDS = {"supplier_id", "notes", "lines"}
PURCHASE_LINE_CREATE_FIELDS = {"product_id", "quantity", "price_unit", "planned_date"}
PURCHASE_LINE_PATCH_FIELDS = {
    "id",
    "product_id",
    "quantity",
    "price_unit",
    "planned_date",
    "remove",
}
RECEIVE_FIELDS = {"lines"}
RECEIVE_LINE_FIELDS = {"move_id", "quantity_done"}
PURCHASE_EDITABLE_STATES = {"draft", "sent", "to approve"}
PURCHASE_STATES = {"draft", "sent", "to approve", "purchase", "done", "cancel"}
PICKING_STATES = {"draft", "waiting", "confirmed", "assigned", "done", "cancel"}
PRODUCT_TYPES = {"consu", "service", "product"}
SUPPLIER_SORTS = {
    "name_asc": "name asc, id asc",
    "name_desc": "name desc, id desc",
    "newest": "create_date desc, id desc",
    "oldest": "create_date asc, id asc",
}
PRODUCT_SORTS = {
    "name_asc": "name asc, id asc",
    "name_desc": "name desc, id desc",
    "newest": "create_date desc, id desc",
    "oldest": "create_date asc, id asc",
}
NEWEST_OLDEST = {
    "newest": "date_order desc, id desc",
    "oldest": "date_order asc, id asc",
}
RECEIPT_SORTS = {
    "newest": "scheduled_date desc, id desc",
    "oldest": "scheduled_date asc, id asc",
}


def _named(record):
    if not record:
        return None
    return {"id": record.id, "name": record.name}


def _strict_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _strict_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _text(value):
    if value in (None, False):
        return ""
    return str(value).strip()


def _reject_unknown(payload, allowed):
    extra = set(payload) - allowed
    if extra:
        return error(
            "INVALID_FIELDS",
            "One or more fields are not allowed.",
            400,
        )
    return None


def _query_positive_int(kwargs, name):
    raw = _query_scalar(kwargs, name)
    if raw in (None, ""):
        return None, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error(
            "INVALID_FILTER",
            "%s must be a positive integer." % name,
            400,
        )
    if value < 1:
        return None, error(
            "INVALID_FILTER",
            "%s must be a positive integer." % name,
            400,
        )
    return value, None


def _parse_bool_flag(kwargs, name):
    raw = _query_scalar(kwargs, name)
    if raw in (None, ""):
        return False, None
    text = str(raw).strip().lower()
    if text not in ("true", "false"):
        return None, error(
            "INVALID_FILTER",
            "%s must be true or false." % name,
            400,
        )
    return text == "true", None


def _parse_datetime(value, name):
    if value in (None, "", False):
        return None, None
    if not isinstance(value, str):
        return None, error(
            "INVALID_FIELDS",
            "%s must be a date string (YYYY-MM-DD)." % name,
            400,
        )
    text = value.strip()
    if not text:
        return None, None
    if "T" in text:
        text = text.replace("T", " ", 1)
    if "+" in text[10:]:
        text = text.split("+", 1)[0].strip()
    elif text.endswith("Z"):
        text = text[:-1].strip()
    try:
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d"), None
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S"), None
    except ValueError:
        return None, error(
            "INVALID_FIELDS",
            "%s must be a date string (YYYY-MM-DD)." % name,
            400,
        )


def _native_error(code, exc, status=400):
    message = exc.args[0] if exc.args else str(exc)
    if not isinstance(message, str):
        message = str(message)
    return error(code, message, status)


def _company_partner_domain(env):
    return [
        "|",
        ("company_id", "=", False),
        ("company_id", "=", env.company.id),
    ]


def supplier_domain(env):
    return [
        ("supplier_rank", ">", 0),
        ("active", "=", True),
    ] + _company_partner_domain(env)


def owned_purchase_domain(user):
    return [
        ("user_id", "=", user.id),
        ("company_id", "=", user.env.company.id),
    ]


def _supplier(env, supplier_id):
    try:
        supplier_id = int(supplier_id)
    except (TypeError, ValueError):
        return env["res.partner"].browse()
    return env["res.partner"].sudo().search(
        [("id", "=", supplier_id)] + supplier_domain(env),
        limit=1,
    )


def _owned_purchase(user, purchase_id):
    env = user.env
    try:
        purchase_id = int(purchase_id)
    except (TypeError, ValueError):
        return env["purchase.order"].browse()
    return env["purchase.order"].sudo().search(
        [("id", "=", purchase_id)] + owned_purchase_domain(user),
        limit=1,
    )


def _owned_receipt(user, picking_id):
    env = user.env
    try:
        picking_id = int(picking_id)
    except (TypeError, ValueError):
        return env["stock.picking"].browse()
    return env["stock.picking"].sudo().search(
        [("id", "=", picking_id)] + receipt_domain(user),
        limit=1,
    )


def receipt_domain(user):
    domain = [
        ("picking_type_code", "=", "incoming"),
        ("company_id", "=", user.env.company.id),
    ]
    Picking = user.env["stock.picking"].sudo()
    if "purchase_id" in Picking._fields:
        domain += [
            ("purchase_id", "!=", False),
            ("purchase_id.user_id", "=", user.id),
        ]
    else:
        domain += [
            ("move_ids.purchase_line_id", "!=", False),
            ("move_ids.purchase_line_id.order_id.user_id", "=", user.id),
        ]
    return domain


def serialize_supplier(partner):
    return {
        "id": partner.id,
        "name": partner.name or "",
        "phone": partner.phone or None,
        "mobile": partner.mobile or None,
        "email": partner.email or None,
        "street": partner.street or None,
        "street2": partner.street2 or None,
        "city": partner.city or None,
        "zip": partner.zip or None,
        "state": _named(partner.state_id),
        "country": (
            {
                "id": partner.country_id.id,
                "name": partner.country_id.name,
                "code": partner.country_id.code or None,
            }
            if partner.country_id
            else None
        ),
        "vat": partner.vat or None,
        "comment": partner.comment or None,
        "supplier_rank": partner.supplier_rank,
        "active": bool(partner.active),
    }


def _last_purchase_price(product):
    Line = product.env["purchase.order.line"].sudo()
    line = Line.search(
        [
            ("product_id", "=", product.id),
            ("order_id.company_id", "=", product.env.company.id),
            ("state", "in", ("purchase", "done")),
        ],
        order="id desc",
        limit=1,
    )
    if not line:
        return None
    return float(line.price_unit or 0.0)


def serialize_buyer_product(product):
    template = product.product_tmpl_id
    purchase_uom = product.uom_po_id or product.uom_id
    return {
        "id": product.id,
        "template_id": template.id,
        "name": product.display_name or product.name,
        "default_code": product.default_code or None,
        "barcode": product.barcode or None,
        "category": serialize_category(template.categ_id) if template.categ_id else None,
        "brand": serialize_brand(template.almohit_brand_id) if template.almohit_brand_id else None,
        "uom": _named(product.uom_id),
        "purchase_uom": _named(purchase_uom),
        "image_url": image_url(product, "image_256") or image_url(template, "image_256"),
        "active": bool(product.active and template.active),
        "type": product.type,
        "standard_cost": float(product.standard_price or 0.0),
        "last_purchase_price": _last_purchase_price(product),
        "purchase_ok": bool(product.purchase_ok),
    }


def _move_done_qty(move):
    if "quantity" in move._fields:
        return float(move.quantity or 0.0)
    if "quantity_done" in move._fields:
        return float(move.quantity_done or 0.0)
    return float(sum(move.move_line_ids.mapped("qty_done") or [0.0]))


def _set_move_done_qty(move, qty):
    if "quantity" in move._fields:
        move.quantity = qty
        return
    if "quantity_done" in move._fields:
        move.quantity_done = qty
        return
    lines = move.move_line_ids
    if not lines:
        move._action_assign()
        lines = move.move_line_ids
    if not lines:
        move.move_line_ids = [(0, 0, {
            "product_id": move.product_id.id,
            "product_uom_id": move.product_uom.id,
            "qty_done": qty,
            "location_id": move.location_id.id,
            "location_dest_id": move.location_dest_id.id,
            "picking_id": move.picking_id.id,
        })]
        return
    lines[0].qty_done = qty
    for extra in lines[1:]:
        extra.qty_done = 0.0


def serialize_purchase_line(line):
    received = float(line.qty_received or 0.0) if "qty_received" in line._fields else 0.0
    ordered = float(line.product_qty or 0.0)
    return {
        "id": line.id,
        "product": {
            "id": line.product_id.id,
            "name": line.product_id.display_name,
            "default_code": line.product_id.default_code or None,
        },
        "quantity": ordered,
        "uom": _named(line.product_uom),
        "price_unit": float(line.price_unit or 0.0),
        "subtotal": float(line.price_subtotal or 0.0),
        "planned_date": line.date_planned or None,
        "received_qty": received,
        "remaining_qty": max(ordered - received, 0.0),
    }


def serialize_receipt_line(move):
    ordered = float(move.product_uom_qty or 0.0)
    received = _move_done_qty(move)
    if move.state == "done":
        received = ordered if received <= 0 else received
    return {
        "move_id": move.id,
        "product": {
            "id": move.product_id.id,
            "name": move.product_id.display_name,
            "default_code": move.product_id.default_code or None,
        },
        "ordered_qty": ordered,
        "received_qty": received,
        "remaining_qty": max(ordered - received, 0.0),
        "uom": _named(move.product_uom),
    }


def _purchase_of_picking(picking):
    if "purchase_id" in picking._fields and picking.purchase_id:
        return picking.purchase_id
    lines = picking.move_ids.mapped("purchase_line_id.order_id")
    return lines[:1]


def serialize_receipt(picking, detail=False):
    purchase = _purchase_of_picking(picking)
    item = {
        "id": picking.id,
        "name": picking.name,
        "state": picking.state,
        "scheduled_date": picking.scheduled_date or None,
        "date_done": picking.date_done or None,
        "supplier": _named(picking.partner_id),
        "purchase": _named(purchase) if purchase else None,
    }
    if detail:
        moves = picking.move_ids.filtered(lambda move: not move.scrapped)
        item["lines"] = [serialize_receipt_line(move) for move in moves]
        item["backorder_id"] = picking.backorder_id.id if picking.backorder_id else None
        backorders = picking.backorder_ids if "backorder_ids" in picking._fields else picking.env["stock.picking"]
        item["backorders"] = [_named(row) for row in backorders]
    return item


def serialize_purchase(order, detail=False):
    pickings = order.picking_ids if "picking_ids" in order._fields else order.env["stock.picking"]
    incoming = pickings.filtered(lambda picking: picking.picking_type_code == "incoming")
    item = {
        "id": order.id,
        "name": order.name,
        "supplier": _named(order.partner_id),
        "state": order.state,
        "date_order": order.date_order or None,
        "currency": _named(order.currency_id),
        "amount_untaxed": float(order.amount_untaxed or 0.0),
        "amount_tax": float(order.amount_tax or 0.0),
        "amount_total": float(order.amount_total or 0.0),
        "buyer": _named(order.user_id),
        "receipt_count": len(incoming),
        "receipt_states": sorted({picking.state for picking in incoming}),
    }
    if detail:
        item["notes"] = order.notes or None
        item["lines"] = [
            serialize_purchase_line(line)
            for line in order.order_line.filtered(lambda rec: not rec.display_type)
        ]
        item["receipts"] = [serialize_receipt(picking) for picking in incoming]
    return item


def _parse_supplier_values(env, payload, partial=False):
    err = _reject_unknown(payload, SUPPLIER_FIELDS)
    if err:
        return None, err
    values = {}
    if "name" in payload or not partial:
        name = _text(payload.get("name"))
        if not name:
            return None, error("NAME_REQUIRED", "Supplier name is required.", 400)
        values["name"] = name
    for field in ("phone", "mobile", "email", "street", "street2", "city", "zip"):
        if field in payload:
            values[field] = _text(payload.get(field)) or False
    vat = None
    if "vat" in payload or "tax_number" in payload:
        vat_value = payload["vat"] if "vat" in payload else payload.get("tax_number")
        tax_value = payload.get("tax_number") if "tax_number" in payload else vat_value
        if "vat" in payload and "tax_number" in payload and _text(payload.get("vat")) != _text(payload.get("tax_number")):
            return None, error(
                "INVALID_FIELDS",
                "vat and tax_number must match when both are sent.",
                400,
            )
        vat = _text(vat_value if vat_value is not None else tax_value) or False
        values["vat"] = vat
    if "comment" in payload or "notes" in payload:
        comment_value = payload["comment"] if "comment" in payload else payload.get("notes")
        if "comment" in payload and "notes" in payload and _text(payload.get("comment")) != _text(payload.get("notes")):
            return None, error(
                "INVALID_FIELDS",
                "comment and notes must match when both are sent.",
                400,
            )
        values["comment"] = _text(comment_value) or False
    if "country_id" in payload:
        country_id = payload.get("country_id")
        if country_id in (None, False, ""):
            values["country_id"] = False
        else:
            country_id = _strict_int(country_id)
            country = env["res.country"].sudo().browse(country_id) if country_id else env["res.country"]
            if not country_id or not country.exists():
                return None, error("COUNTRY_NOT_FOUND", "Country not found.", 400)
            values["country_id"] = country.id
    if "state_id" in payload:
        state_id = payload.get("state_id")
        if state_id in (None, False, ""):
            values["state_id"] = False
        else:
            state_id = _strict_int(state_id)
            state = env["res.country.state"].sudo().browse(state_id) if state_id else env["res.country.state"]
            if not state_id or not state.exists():
                return None, error("STATE_NOT_FOUND", "State not found.", 400)
            values["state_id"] = state.id
    country_id = values.get("country_id")
    state_id = values.get("state_id")
    if state_id:
        state = env["res.country.state"].sudo().browse(state_id)
        if country_id and state.country_id.id != country_id:
            return None, error(
                "STATE_COUNTRY_MISMATCH",
                "State does not belong to the selected country.",
                400,
            )
    return values, None


def list_suppliers(user, kwargs):
    env = user.env
    page, page_size, err = parse_pagination(kwargs)
    if err:
        return err
    domain = supplier_domain(env)
    q = _text(_query_scalar(kwargs, "q"))
    if q:
        domain += [
            "|",
            "|",
            "|",
            "|",
            ("name", "ilike", q),
            ("phone", "ilike", q),
            ("mobile", "ilike", q),
            ("email", "ilike", q),
            ("vat", "ilike", q),
        ]
    country_id, err = _query_positive_int(kwargs, "country_id")
    if err:
        return err
    if country_id:
        domain.append(("country_id", "=", country_id))
    state_id, err = _query_positive_int(kwargs, "state_id")
    if err:
        return err
    if state_id:
        domain.append(("state_id", "=", state_id))
    raw_sort = _query_scalar(kwargs, "sort")
    order = "name, id"
    if raw_sort not in (None, ""):
        order = SUPPLIER_SORTS.get(str(raw_sort).strip())
        if not order:
            return error(
                "INVALID_SORT",
                "sort must be one of: name_asc, name_desc, newest, oldest.",
                400,
            )
    suppliers = env["res.partner"].sudo().search(domain, order=order)
    return paginated_success(suppliers, serialize_supplier, page, page_size)


def get_supplier(user, supplier_id):
    supplier = _supplier(user.env, supplier_id)
    if not supplier:
        return error("SUPPLIER_NOT_FOUND", "Supplier not found.", 404)
    return success(serialize_supplier(supplier))


def create_supplier(user, payload):
    values, err = _parse_supplier_values(user.env, payload, partial=False)
    if err:
        return err
    values.update({
        "supplier_rank": 1,
        "customer_rank": 0,
        "company_id": user.env.company.id,
        "type": "contact",
    })
    try:
        supplier = user.env["res.partner"].sudo().create(values)
    except (UserError, ValidationError) as exc:
        return _native_error("INVALID_FIELDS", exc)
    return success(serialize_supplier(supplier), status=201)


def patch_supplier(user, supplier_id, payload):
    supplier = _supplier(user.env, supplier_id)
    if not supplier:
        return error("SUPPLIER_NOT_FOUND", "Supplier not found.", 404)
    values, err = _parse_supplier_values(user.env, payload, partial=True)
    if err:
        return err
    if not values:
        return success(serialize_supplier(supplier))
    try:
        supplier.write(values)
    except (UserError, ValidationError) as exc:
        return _native_error("INVALID_FIELDS", exc)
    return success(serialize_supplier(supplier))


def _purchaseable_product(env, product_id):
    product_id = _strict_int(product_id)
    if not product_id:
        return env["product.product"].browse()
    return env["product.product"].sudo().search(
        [
            ("id", "=", product_id),
            ("active", "=", True),
            ("purchase_ok", "=", True),
            ("product_tmpl_id.active", "=", True),
        ],
        limit=1,
    )


def _buyer_product_domain(env, kwargs):
    domain = [
        ("active", "=", True),
        ("purchase_ok", "=", True),
        ("product_tmpl_id.active", "=", True),
    ]
    category_id, err = _query_positive_int(kwargs, "category_id")
    if err:
        return None, err
    include_children, err = _parse_bool_flag(kwargs, "include_children")
    if err:
        return None, err
    if include_children and not category_id:
        return None, error(
            "INVALID_FILTER",
            "include_children requires category_id.",
            400,
        )
    if category_id:
        if include_children:
            domain.append(("categ_id", "child_of", category_id))
        else:
            domain.append(("categ_id", "=", category_id))
    brand_id, err = _query_positive_int(kwargs, "brand_id")
    if err:
        return None, err
    if brand_id:
        domain.append(("product_tmpl_id.almohit_brand_id", "=", brand_id))
    q = _text(_query_scalar(kwargs, "q"))
    if q:
        domain += [
            "|",
            "|",
            "|",
            ("name", "ilike", q),
            ("default_code", "ilike", q),
            ("barcode", "ilike", q),
            ("product_tmpl_id.name", "ilike", q),
        ]
    return domain, None


def list_buyer_products(user, kwargs):
    env = user.env
    page, page_size, err = parse_pagination(kwargs)
    if err:
        return err
    domain, err = _buyer_product_domain(env, kwargs)
    if err:
        return err
    raw_sort = _query_scalar(kwargs, "sort")
    order = "name, id"
    if raw_sort not in (None, ""):
        order = PRODUCT_SORTS.get(str(raw_sort).strip())
        if not order:
            return error(
                "INVALID_SORT",
                "sort must be one of: name_asc, name_desc, newest, oldest.",
                400,
            )
    Product = env["product.product"].sudo()
    total = Product.search_count(domain)
    products = Product.search(
        domain,
        offset=(page - 1) * page_size,
        limit=page_size,
        order=order,
    )
    pages = 0 if total == 0 else (total + page_size - 1) // page_size
    return success({
        "items": [serialize_buyer_product(product) for product in products],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        },
    })


def get_buyer_product(user, product_id):
    product = _purchaseable_product(user.env, product_id)
    if not product:
        return error("BUYER_PRODUCT_NOT_FOUND", "Product not found.", 404)
    return success(serialize_buyer_product(product))


def create_buyer_product(user, payload):
    err = _reject_unknown(payload, PRODUCT_CREATE_FIELDS)
    if err:
        return err
    env = user.env.sudo()
    name = _text(payload.get("name"))
    if not name:
        return error("NAME_REQUIRED", "Product name is required.", 400)
    vals = {
        "name": name,
        "purchase_ok": True,
        "sale_ok": True,
        "active": True,
        "type": "product",
    }
    Template = env["product.template"]
    if "detailed_type" in Template._fields:
        vals["detailed_type"] = "product"
    product_type = payload.get("type")
    if product_type not in (None, ""):
        product_type = _text(product_type)
        if product_type not in PRODUCT_TYPES:
            return error(
                "INVALID_FIELDS",
                "type must be one of: consu, service, product.",
                400,
            )
        vals["type"] = product_type
        if "detailed_type" in Template._fields:
            vals["detailed_type"] = product_type
    if "default_code" in payload:
        vals["default_code"] = _text(payload.get("default_code")) or False
    if "barcode" in payload:
        vals["barcode"] = _text(payload.get("barcode")) or False
    if "purchase_description" in payload:
        vals["description_purchase"] = _text(payload.get("purchase_description")) or False
    if payload.get("category_id") not in (None, "", False):
        category_id = _strict_int(payload.get("category_id"))
        category = env["product.category"].browse(category_id) if category_id else env["product.category"]
        if not category_id or not category.exists():
            return error("CATEGORY_NOT_FOUND", "Category not found.", 400)
        vals["categ_id"] = category.id
    if payload.get("brand_id") not in (None, "", False):
        brand_id = _strict_int(payload.get("brand_id"))
        brand = env["almohit.product.brand"].browse(brand_id) if brand_id else env["almohit.product.brand"]
        if not brand_id or not brand.exists() or not brand.active:
            return error("BRAND_NOT_FOUND", "Brand not found.", 400)
        vals["almohit_brand_id"] = brand.id
    Uom = env["uom.uom"]
    if payload.get("uom_id") not in (None, "", False):
        uom_id = _strict_int(payload.get("uom_id"))
        uom = Uom.browse(uom_id) if uom_id else Uom
        if not uom_id or not uom.exists():
            return error("UOM_NOT_FOUND", "Unit of measure not found.", 400)
        vals["uom_id"] = uom.id
    if payload.get("uom_po_id") not in (None, "", False):
        uom_po_id = _strict_int(payload.get("uom_po_id"))
        uom_po = Uom.browse(uom_po_id) if uom_po_id else Uom
        if not uom_po_id or not uom_po.exists():
            return error("UOM_NOT_FOUND", "Purchase unit of measure not found.", 400)
        vals["uom_po_id"] = uom_po.id
    elif vals.get("uom_id"):
        vals["uom_po_id"] = vals["uom_id"]
    image_b64, image_err = _decode_image(payload, max_bytes=PRODUCT_IMAGE_MAX_BYTES)
    if image_err:
        return image_err
    if image_b64:
        vals["image_1920"] = image_b64
    try:
        template = Template.create(vals)
    except (UserError, ValidationError) as exc:
        return _native_error("INVALID_FIELDS", exc)
    product = template.product_variant_id
    if not product:
        return error("BUYER_PRODUCT_NOT_FOUND", "Product variant was not created.", 500)
    return success(serialize_buyer_product(product), status=201)


def _line_vals(env, payload, required=True):
    if "product_id" not in payload:
        return None, error("INVALID_FIELDS", "product_id is required.", 400)
    product = _purchaseable_product(env, payload.get("product_id"))
    if not product:
        return None, error("BUYER_PRODUCT_NOT_FOUND", "Product not found.", 404)
    quantity = _strict_float(payload.get("quantity"))
    if quantity is None or quantity <= 0:
        return None, error(
            "INVALID_FIELDS",
            "quantity must be a number greater than 0.",
            400,
        )
    price_unit = _strict_float(payload.get("price_unit"))
    if price_unit is None or price_unit < 0:
        return None, error(
            "INVALID_FIELDS",
            "price_unit must be a number greater than or equal to 0.",
            400,
        )
    planned, err = _parse_datetime(payload.get("planned_date"), "planned_date")
    if err:
        return None, err
    uom = product.uom_po_id or product.uom_id
    vals = {
        "product_id": product.id,
        "name": product.display_name,
        "product_qty": quantity,
        "price_unit": price_unit,
        "product_uom": uom.id if uom else False,
    }
    if planned:
        vals["date_planned"] = planned
    elif required:
        vals["date_planned"] = datetime.utcnow()
    return vals, None


def _parse_create_lines(env, lines):
    if not isinstance(lines, list):
        return None, error("INVALID_FIELDS", "lines must be an array.", 400)
    commands = []
    for line in lines:
        if not isinstance(line, dict):
            return None, error("INVALID_FIELDS", "Each purchase line must be an object.", 400)
        err = _reject_unknown(line, PURCHASE_LINE_CREATE_FIELDS)
        if err:
            return None, err
        vals, err = _line_vals(env, line, required=True)
        if err:
            return None, err
        commands.append((0, 0, vals))
    return commands, None


def create_purchase(user, payload):
    err = _reject_unknown(payload, PURCHASE_CREATE_FIELDS)
    if err:
        return err
    env = user.env
    supplier_id = _strict_int(payload.get("supplier_id"))
    supplier = _supplier(env, supplier_id)
    if not supplier:
        return error("SUPPLIER_NOT_FOUND", "Supplier not found.", 404)
    date_order, err = _parse_datetime(payload.get("date_order"), "date_order")
    if err:
        return err
    lines, err = _parse_create_lines(env, payload.get("lines") or [])
    if err:
        return err
    currency = getattr(supplier, "property_purchase_currency_id", False) or env.company.currency_id
    values = {
        "partner_id": supplier.id,
        "user_id": user.id,
        "company_id": env.company.id,
        "currency_id": currency.id if currency else False,
        "order_line": lines,
    }
    if date_order:
        values["date_order"] = date_order
    if "notes" in payload:
        values["notes"] = _text(payload.get("notes")) or False
    try:
        order = env["purchase.order"].sudo().create(values)
    except (UserError, ValidationError) as exc:
        return _native_error("INVALID_FIELDS", exc)
    return success(serialize_purchase(order, detail=True), status=201)


def list_purchases(user, kwargs):
    page, page_size, err = parse_pagination(kwargs)
    if err:
        return err
    domain = owned_purchase_domain(user)
    q = _text(_query_scalar(kwargs, "q"))
    if q:
        domain += [
            "|",
            "|",
            ("name", "ilike", q),
            ("partner_id.name", "ilike", q),
            ("partner_ref", "ilike", q),
        ]
    raw_state = _query_scalar(kwargs, "state")
    if raw_state not in (None, ""):
        state = str(raw_state).strip().lower()
        if state not in PURCHASE_STATES:
            return error(
                "INVALID_FILTER",
                "state must be one of: draft, sent, to approve, purchase, done, cancel.",
                400,
            )
        domain.append(("state", "=", state))
    supplier_id, err = _query_positive_int(kwargs, "supplier_id")
    if err:
        return err
    if supplier_id:
        domain.append(("partner_id", "=", supplier_id))
    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("date_order", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("date_order", "<=", datetime.combine(date_to, time(23, 59, 59))))
    raw_sort = _query_scalar(kwargs, "sort")
    order_by = "id desc"
    if raw_sort not in (None, ""):
        order_by = NEWEST_OLDEST.get(str(raw_sort).strip())
        if not order_by:
            return error("INVALID_SORT", "sort must be one of: newest, oldest.", 400)
    orders = user.env["purchase.order"].sudo().search(domain, order=order_by)
    return paginated_success(
        orders,
        lambda order: serialize_purchase(order),
        page,
        page_size,
    )


def get_purchase(user, purchase_id):
    order = _owned_purchase(user, purchase_id)
    if not order:
        return error("PURCHASE_NOT_FOUND", "Purchase order not found.", 404)
    return success(serialize_purchase(order, detail=True))


def patch_purchase(user, purchase_id, payload):
    err = _reject_unknown(payload, PURCHASE_PATCH_FIELDS)
    if err:
        return err
    order = _owned_purchase(user, purchase_id)
    if not order:
        return error("PURCHASE_NOT_FOUND", "Purchase order not found.", 404)
    if order.state not in PURCHASE_EDITABLE_STATES:
        return error(
            "PURCHASE_NOT_EDITABLE",
            "This purchase order can no longer be edited.",
            400,
        )
    values = {}
    if "supplier_id" in payload:
        supplier = _supplier(user.env, payload.get("supplier_id"))
        if not supplier:
            return error("SUPPLIER_NOT_FOUND", "Supplier not found.", 404)
        values["partner_id"] = supplier.id
    if "notes" in payload:
        values["notes"] = _text(payload.get("notes")) or False
    if "lines" in payload:
        if not isinstance(payload.get("lines"), list):
            return error("INVALID_FIELDS", "lines must be an array.", 400)
        commands = []
        existing = {line.id: line for line in order.order_line}
        for line in payload.get("lines"):
            if not isinstance(line, dict):
                return error("INVALID_FIELDS", "Each purchase line must be an object.", 400)
            err = _reject_unknown(line, PURCHASE_LINE_PATCH_FIELDS)
            if err:
                return err
            line_id = line.get("id")
            if line.get("remove") is True:
                line_id = _strict_int(line_id)
                if not line_id or line_id not in existing:
                    return error("PURCHASE_NOT_FOUND", "Purchase order line not found.", 404)
                commands.append((2, line_id))
                continue
            if line_id in (None, "", False):
                vals, err = _line_vals(user.env, line, required=True)
                if err:
                    return err
                commands.append((0, 0, vals))
                continue
            line_id = _strict_int(line_id)
            if not line_id or line_id not in existing:
                return error("PURCHASE_NOT_FOUND", "Purchase order line not found.", 404)
            rec = existing[line_id]
            update = {}
            if "product_id" in line:
                product = _purchaseable_product(user.env, line.get("product_id"))
                if not product:
                    return error("BUYER_PRODUCT_NOT_FOUND", "Product not found.", 404)
                update["product_id"] = product.id
                update["name"] = product.display_name
                update["product_uom"] = (product.uom_po_id or product.uom_id).id
            if "quantity" in line:
                quantity = _strict_float(line.get("quantity"))
                if quantity is None or quantity <= 0:
                    return error(
                        "INVALID_FIELDS",
                        "quantity must be a number greater than 0.",
                        400,
                    )
                update["product_qty"] = quantity
            if "price_unit" in line:
                price_unit = _strict_float(line.get("price_unit"))
                if price_unit is None or price_unit < 0:
                    return error(
                        "INVALID_FIELDS",
                        "price_unit must be a number greater than or equal to 0.",
                        400,
                    )
                update["price_unit"] = price_unit
            if "planned_date" in line:
                planned, err = _parse_datetime(line.get("planned_date"), "planned_date")
                if err:
                    return err
                update["date_planned"] = planned or False
            if update:
                commands.append((1, rec.id, update))
        values["order_line"] = commands
    if not values:
        return success(serialize_purchase(order, detail=True))
    try:
        order.write(values)
    except (UserError, ValidationError) as exc:
        return _native_error("PURCHASE_NOT_EDITABLE", exc)
    return success(serialize_purchase(order, detail=True))


def confirm_purchase(user, purchase_id):
    order = _owned_purchase(user, purchase_id)
    if not order:
        return error("PURCHASE_NOT_FOUND", "Purchase order not found.", 404)
    if order.state not in PURCHASE_EDITABLE_STATES:
        return error(
            "PURCHASE_NOT_EDITABLE",
            "This purchase order is already confirmed.",
            400,
        )
    try:
        order.button_confirm()
    except (UserError, ValidationError) as exc:
        return _native_error("PURCHASE_CONFIRM_FAILED", exc)
    return success(serialize_purchase(order, detail=True))


def list_receipts(user, kwargs):
    page, page_size, err = parse_pagination(kwargs)
    if err:
        return err
    domain = receipt_domain(user)
    q = _text(_query_scalar(kwargs, "q"))
    if q:
        domain += [
            "|",
            "|",
            ("name", "ilike", q),
            ("origin", "ilike", q),
            ("partner_id.name", "ilike", q),
        ]
    raw_status = _query_scalar(kwargs, "status")
    if raw_status not in (None, ""):
        status = str(raw_status).strip().lower()
        if status == "ready":
            status = "assigned"
        if status not in PICKING_STATES:
            return error(
                "INVALID_FILTER",
                "status must be one of: draft, waiting, confirmed, assigned, done, cancel.",
                400,
            )
        domain.append(("state", "=", status))
    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("scheduled_date", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("scheduled_date", "<=", datetime.combine(date_to, time(23, 59, 59))))
    raw_sort = _query_scalar(kwargs, "sort")
    order_by = "id desc"
    if raw_sort not in (None, ""):
        order_by = RECEIPT_SORTS.get(str(raw_sort).strip())
        if not order_by:
            return error("INVALID_SORT", "sort must be one of: newest, oldest.", 400)
    pickings = user.env["stock.picking"].sudo().search(domain, order=order_by)
    return paginated_success(
        pickings,
        lambda picking: serialize_receipt(picking),
        page,
        page_size,
    )


def get_receipt(user, picking_id):
    picking = _owned_receipt(user, picking_id)
    if not picking:
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    return success(serialize_receipt(picking, detail=True))


def _finish_validate(picking, result):
    for _ in range(6):
        if not isinstance(result, dict):
            return None
        model = result.get("res_model")
        if not model:
            return None
        ctx = result.get("context") or {}
        Wizard = picking.env[model].with_context(ctx)
        if result.get("res_id"):
            wizard = Wizard.browse(result["res_id"])
        else:
            wizard = Wizard.create({})
        if hasattr(wizard, "process"):
            result = wizard.process()
        elif hasattr(wizard, "process_cancel_backorder"):
            result = wizard.process()
        elif hasattr(wizard, "action_confirm"):
            result = wizard.action_confirm()
        else:
            return None
    return None


def receive_receipt(user, picking_id, payload):
    err = _reject_unknown(payload, RECEIVE_FIELDS)
    if err:
        return err
    picking = _owned_receipt(user, picking_id)
    if not picking:
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    if picking.state in ("done", "cancel"):
        return error(
            "RECEIPT_NOT_READY",
            "This receipt cannot be processed.",
            400,
        )
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return error("INVALID_FIELDS", "lines must be a non-empty array.", 400)
    try:
        if picking.state == "draft" and hasattr(picking, "action_confirm"):
            picking.action_confirm()
        if picking.state in ("confirmed", "waiting", "assigned") and hasattr(picking, "action_assign"):
            picking.action_assign()
    except (UserError, ValidationError) as exc:
        return _native_error("RECEIPT_NOT_READY", exc)
    moves = {move.id: move for move in picking.move_ids}
    for line in lines:
        if not isinstance(line, dict):
            return error("INVALID_FIELDS", "Each receipt line must be an object.", 400)
        err = _reject_unknown(line, RECEIVE_LINE_FIELDS)
        if err:
            return err
        move_id = _strict_int(line.get("move_id"))
        move = moves.get(move_id)
        if not move:
            return error("RECEIPT_NOT_FOUND", "Receipt line not found.", 404)
        qty = _strict_float(line.get("quantity_done"))
        if qty is None or qty < 0:
            return error(
                "INVALID_RECEIPT_QUANTITY",
                "quantity_done must be a number greater than or equal to 0.",
                400,
            )
        try:
            _set_move_done_qty(move, qty)
        except (UserError, ValidationError) as exc:
            return _native_error("INVALID_RECEIPT_QUANTITY", exc)
    try:
        result = picking.button_validate()
        _finish_validate(picking, result)
    except (UserError, ValidationError) as exc:
        return _native_error("INVALID_RECEIPT_QUANTITY", exc)
    picking.invalidate_recordset()
    data = serialize_receipt(picking, detail=True)
    backorders = []
    if "backorder_ids" in picking._fields:
        backorders = [_named(row) for row in picking.backorder_ids]
    data["backorders"] = backorders
    return success(data)
