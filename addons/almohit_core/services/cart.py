from odoo.tools.float_utils import float_compare

from .stock import available_qty


def parse_quantity(payload):
    if not isinstance(payload, dict) or "quantity" not in payload:
        return None
    try:
        quantity = float(payload.get("quantity"))
    except (TypeError, ValueError):
        return None
    if quantity != quantity:
        return None
    return quantity


def saleable_variant(env, product_id):
    # Public/portal users may lack product.product ACL.
    # sudo() is scoped to one id, then active + saleable template checks.
    product = env["product.product"].sudo().search(
        [("id", "=", product_id)],
        limit=1,
    )
    if not product:
        return None, "PRODUCT_NOT_FOUND"
    template = product.product_tmpl_id
    if (
        not product.active
        or not template.active
        or not template.sale_ok
    ):
        return None, "PRODUCT_UNAVAILABLE"
    return product, None


def min_order_qty(product):
    return product.product_tmpl_id.almohit_min_order_qty or 1.0


def quantity_below_moq(quantity, moq, rounding=0.01):
    return float_compare(quantity, moq, precision_rounding=rounding) < 0


def serialize_empty_cart(currency):
    return {
        "id": None,
        "currency": (
            {
                "id": currency.id,
                "name": currency.name,
            }
            if currency
            else None
        ),
        "items": [],
        "amount_untaxed": 0.0,
        "amount_tax": 0.0,
        "amount_total": 0.0,
    }


def serialize_cart(order):
    currency = order.currency_id
    items = []
    for line in order.order_line.filtered(lambda rec: not rec.display_type):
        product = line.product_id
        template = product.product_tmpl_id
        items.append({
            "line_id": line.id,
            "product_id": product.id,
            "template_id": template.id,
            "name": line.name,
            "default_code": product.default_code or None,
            "quantity": line.product_uom_qty,
            "uom": (
                {
                    "id": line.product_uom.id,
                    "name": line.product_uom.name,
                }
                if line.product_uom
                else None
            ),
            "min_order_qty": template.almohit_min_order_qty,
            "available_qty": available_qty(product, order.warehouse_id),
            "unit_price": line.price_unit,
            "subtotal": line.price_subtotal,
        })
    return {
        "id": order.id,
        "currency": (
            {
                "id": currency.id,
                "name": currency.name,
            }
            if currency
            else None
        ),
        "items": items,
        "amount_untaxed": order.amount_untaxed,
        "amount_tax": order.amount_tax,
        "amount_total": order.amount_total,
    }
