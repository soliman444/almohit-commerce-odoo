from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .addresses import serialize_address
from .payments import order_allows_distribution


PICKING_STATUS_MAP = {
    "draft": "pending",
    "waiting": "waiting",
    "confirmed": "waiting",
    "assigned": "ready",
    "done": "delivered",
    "cancel": "cancelled",
}


def is_inventory_managed(product):
    return product.type == "product"


def company_warehouse(env):
    return env["stock.warehouse"].sudo().search(
        [("company_id", "=", env.company.id)],
        limit=1,
    )


def _product_for_qty(product, warehouse=None):
    product = product.sudo()
    if warehouse:
        return product.with_context(warehouse=warehouse.id)
    return product


def free_qty(product, warehouse=None):
    return _product_for_qty(product, warehouse).free_qty


def product_in_stock(product, warehouse=None):
    if not is_inventory_managed(product):
        return True
    qty = free_qty(product, warehouse)
    rounding = product.uom_id.rounding or 0.01
    return float_compare(qty, 0.0, precision_rounding=rounding) > 0


def template_in_stock(template, warehouse=None):
    variants = template.product_variant_ids.filtered("active")
    if not variants:
        return product_in_stock(template, warehouse)
    return any(product_in_stock(variant, warehouse) for variant in variants)


def availability_payload(in_stock, qty=None):
    payload = {"in_stock": bool(in_stock)}
    if qty is not None:
        payload["qty_available"] = float(qty)
    return payload


def available_qty(product, warehouse=None):
    if not is_inventory_managed(product):
        return None
    return float(free_qty(product, warehouse) or 0.0)


def over_stock_error(product, quantity, warehouse=None):
    available = available_qty(product, warehouse)
    if available is None:
        return None
    rounding = product.uom_id.rounding or 0.01
    if float_compare(available, quantity, precision_rounding=rounding) < 0:
        return (
            "INSUFFICIENT_STOCK",
            "Not enough stock is available for this product.",
            400,
            {
                "product_id": product.id,
                "requested_qty": quantity,
                "available_qty": available,
            },
        )
    return None


def requested_qty_in_product_uom(line, product):
    line_uom = line.product_uom
    product_uom = product.uom_id
    if not line_uom or not product_uom:
        return line.product_uom_qty
    if line_uom == product_uom:
        return line.product_uom_qty
    return line_uom._compute_quantity(
        line.product_uom_qty,
        product_uom,
        rounding_method="HALF-UP",
    )


def insufficient_stock_error(line, product, warehouse=None):
    needed = requested_qty_in_product_uom(line, product)
    return over_stock_error(product, needed, warehouse)


def outgoing_pickings(order):
    return order.sudo().picking_ids.filtered(
        lambda picking: picking.picking_type_id.code == "outgoing"
    ).sorted("id")


def serialize_delivery(order):
    pickings = outgoing_pickings(order).filtered(
        lambda picking: picking.state != "cancel"
    )
    picking = pickings[:1] or outgoing_pickings(order)[:1]
    if not picking:
        return None
    return {
        "status": PICKING_STATUS_MAP.get(picking.state, picking.state),
        "picking_id": picking.id,
        "reference": picking.name,
        "scheduled_date": picking.scheduled_date or None,
        "date_done": picking.date_done or None,
    }


ALLOWED_DELIVERY_STATUSES = {"ready", "delivered"}


def _sale_order(picking):
    sale = picking.sale_id
    if sale:
        return sale
    origin = (picking.origin or "").strip()
    if not origin:
        return picking.env["sale.order"].browse()
    return picking.env["sale.order"].sudo().search(
        [("name", "=", origin), ("company_id", "=", picking.company_id.id)],
        limit=1,
    )


def _move_qty_done(move):
    if hasattr(move, "quantity"):
        return move.quantity
    return getattr(move, "quantity_done", 0.0) or 0.0


def allowed_delivery_actions(picking):
    actions = []
    if picking.state in ("waiting", "confirmed"):
        actions.append("ready")
    if picking.state in ("waiting", "confirmed", "assigned"):
        actions.append("delivered")
    return actions


def serialize_picking(picking):
    sale = _sale_order(picking)
    partner = picking.partner_id
    customer = sale.partner_id if sale else partner
    moves = picking.move_ids.filtered(lambda move: move.state != "cancel")
    return {
        "id": picking.id,
        "name": picking.name,
        "status": PICKING_STATUS_MAP.get(picking.state, picking.state),
        "scheduled_date": picking.scheduled_date or None,
        "date_done": picking.date_done or None,
        "origin": picking.origin or None,
        "order": {"id": sale.id, "name": sale.name} if sale else None,
        "customer": {
            "name": customer.name if customer else None,
            "phone": (customer.phone or customer.mobile) if customer else None,
        },
        "shipping_address": serialize_address(partner) if partner else None,
        "items": [
            {
                "id": move.id,
                "product_id": move.product_id.id,
                "name": move.product_id.display_name,
                "default_code": move.product_id.default_code or None,
                "quantity": move.product_uom_qty,
                "quantity_done": _move_qty_done(move),
                "uom": move.product_uom.display_name if move.product_uom else None,
            }
            for move in moves
        ],
        "allowed_actions": allowed_delivery_actions(picking),
    }


def assigned_outgoing_domain(env, user):
    return [
        ("picking_type_code", "=", "outgoing"),
        ("user_id", "=", user.id),
        ("company_id", "=", env.company.id),
    ]


def picking_allows_distribution(picking):
    sale = _sale_order(picking)
    if sale and sale.almohit_storefront_order:
        return order_allows_distribution(sale)
    return False


def search_assigned_deliveries(env, user):
    pickings = env["stock.picking"].sudo().search(
        assigned_outgoing_domain(env, user),
        order="id desc",
    )
    return pickings.filtered(picking_allows_distribution)


def owned_assigned_delivery(env, user, picking_id):
    try:
        picking_id = int(picking_id)
    except (TypeError, ValueError):
        return env["stock.picking"].sudo().browse()
    picking = env["stock.picking"].sudo().search(
        [("id", "=", picking_id)] + assigned_outgoing_domain(env, user),
        limit=1,
    )
    if picking and not picking_allows_distribution(picking):
        return env["stock.picking"].sudo().browse()
    return picking


def _mark_ready(picking):
    picking = picking.sudo()
    if picking.state == "draft":
        picking.action_confirm()
    if picking.state in ("waiting", "confirmed"):
        picking.action_assign()
    picking.invalidate_recordset()
    if picking.state != "assigned":
        return None, (
            "DELIVERY_NOT_READY",
            "This delivery could not be marked ready.",
            400,
            None,
        )
    return picking, None


def _set_quantities_done(picking):
    if hasattr(picking, "_set_quantities_to_reservation"):
        picking._set_quantities_to_reservation()
        return
    for move in picking.move_ids.filtered(lambda rec: rec.state not in ("done", "cancel")):
        move.quantity = move.product_uom_qty


def _mark_delivered(picking):
    picking = picking.sudo()
    if picking.state == "draft":
        picking.action_confirm()
    if picking.state in ("waiting", "confirmed"):
        picking.action_assign()
    _set_quantities_done(picking)
    try:
        result = picking.with_context(
            skip_immediate=True,
            skip_backorder=True,
            skip_sms=True,
        ).button_validate()
    except UserError:
        return None, (
            "DELIVERY_VALIDATE_FAILED",
            "This delivery could not be marked delivered.",
            400,
            None,
        )
    if isinstance(result, dict) and result.get("res_model"):
        wizard_model = result["res_model"]
        ctx = dict(result.get("context") or {})
        wizard = picking.env[wizard_model].with_context(ctx).create({})
        if hasattr(wizard, "process"):
            wizard.process()
        elif hasattr(wizard, "button_validate"):
            wizard.button_validate()
    picking.invalidate_recordset()
    if picking.state != "done":
        return None, (
            "DELIVERY_VALIDATE_FAILED",
            "This delivery could not be marked delivered.",
            400,
            None,
        )
    return picking, None


def update_assigned_delivery_status(picking, status):
    if not picking_allows_distribution(picking):
        return None, (
            "PAYMENT_REQUIRED",
            "Settle the amount currently due under the payment terms before distributing this order.",
            409,
            None,
        )
    status = (status or "").strip().lower()
    if status not in ALLOWED_DELIVERY_STATUSES:
        return None, (
            "INVALID_DELIVERY_STATUS",
            "Status must be ready or delivered.",
            400,
            None,
        )
    current = PICKING_STATUS_MAP.get(picking.state, picking.state)
    if current == status:
        return picking, None
    if current == "delivered":
        return None, (
            "DELIVERY_ALREADY_DONE",
            "This delivery is already completed.",
            409,
            None,
        )
    if current == "cancelled":
        return None, (
            "DELIVERY_CANCELLED",
            "This delivery was cancelled.",
            409,
            None,
        )
    if status == "ready":
        return _mark_ready(picking)
    return _mark_delivered(picking)
