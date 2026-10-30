"""Shared helpers for warehouse manager stock operations."""
from odoo import SUPERUSER_ID, fields
from odoo.exceptions import UserError, ValidationError

from .api import error
from .customer import get_warehouse_assignment

ICP_RECEIPT_MODE = "almohit.receipt_validation_mode"
RECEIPT_MODES = ("both", "warehouse_only", "buyer_only")


def named(record):
    if not record:
        return None
    return {"id": record.id, "name": record.display_name or record.name or ""}


def receipt_validation_mode(env):
    raw = (
        env["ir.config_parameter"]
        .sudo()
        .get_param(ICP_RECEIPT_MODE, "both")
        or "both"
    ).strip().lower()
    return raw if raw in RECEIPT_MODES else "both"


def assignment_or_error(partner):
    assignment = get_warehouse_assignment(partner)
    if not assignment or not assignment.warehouse_ids:
        return None, error(
            "WAREHOUSE_ASSIGNMENT_REQUIRED",
            "No warehouses are assigned to this account yet.",
            403,
        )
    return assignment, None


def require_perm(assignment, flag, code):
    if not getattr(assignment, flag, False):
        return error(
            "WAREHOUSE_PERMISSION_DENIED",
            "Missing permission: %s." % code,
            403,
        )
    return None


def allowed_location_ids(warehouses):
    # Include inactive Output/Pack/Input — single-step warehouses often archive
    # those locations while still referencing them on pickings / dispatch.
    Location = warehouses.env["stock.location"].with_context(active_test=False).sudo()
    roots = warehouses.mapped("view_location_id")
    if not roots:
        return []
    return Location.search([("id", "child_of", roots.ids)]).ids


def assert_same_company(record, company):
    """Return an error response if record.company_id mismatches, else None."""
    if not record or not company:
        return None
    rec_company = getattr(record, "company_id", False)
    if rec_company and rec_company.id != company.id:
        return error(
            "CROSS_COMPANY_DENIED",
            "Resource belongs to another company.",
            403,
        )
    return None


def filter_assignment_warehouses(assignment, company=None):
    """Assigned warehouses limited to the active company."""
    warehouses = assignment.warehouse_ids
    company = company or assignment.env.company
    return warehouses.filtered(
        lambda warehouse: not warehouse.company_id or warehouse.company_id == company
    )


def location_in_warehouses(location, warehouses):
    if not location or not warehouses:
        return False
    return location.id in allowed_location_ids(warehouses)


def warehouse_for_location(location, warehouses):
    if not location:
        return warehouses.env["stock.warehouse"]
    Location = location.env["stock.location"].sudo()
    for warehouse in warehouses:
        children = Location.search([("id", "child_of", warehouse.view_location_id.id)]).ids
        if location.id in children:
            return warehouse
    return warehouses.env["stock.warehouse"]


def internal_locations(warehouses):
    Location = warehouses.env["stock.location"].sudo()
    return Location.search([
        ("id", "child_of", warehouses.mapped("view_location_id").ids),
        ("usage", "=", "internal"),
    ])


def strict_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def strict_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def native_error(code, exc, status=400):
    message = str(exc) or code
    return error(code, message, status)


def finish_validate(picking, result):
    for _ in range(8):
        if not isinstance(result, dict):
            return None
        model = result.get("res_model")
        if not model:
            return None
        ctx = result.get("context") or {}
        Wizard = picking.env[model].with_context(**ctx)
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


def set_move_done_qty(move, qty):
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


def move_done_qty(move):
    if "quantity" in move._fields:
        return float(move.quantity or 0.0)
    if "quantity_done" in move._fields:
        return float(move.quantity_done or 0.0)
    return float(sum(move.move_line_ids.mapped("qty_done") or [0.0]))


def sudo_picking(picking):
    return picking.with_user(SUPERUSER_ID).sudo()


def ensure_confirmed_assigned(picking):
    if picking.state == "draft" and hasattr(picking, "action_confirm"):
        picking.action_confirm()
    if picking.state in ("confirmed", "waiting", "assigned") and hasattr(picking, "action_assign"):
        picking.action_assign()


def apply_move_destinations(move, destinations, allowed_locations):
    """Apply one or more destination locations for a receive move.

    destinations: list of (location_record, qty)
    `_split(qty)` creates a NEW move with `qty` and reduces the original.
    """
    if not destinations:
        return error("INVALID_FIELDS", "destinations are required.", 400)
    for location, qty in destinations:
        if qty < 0:
            return error("INVALID_QUANTITY", "Quantity must be >= 0.", 400)
        if location.id not in allowed_locations:
            return error(
                "WAREHOUSE_NOT_ALLOWED",
                "Location is outside assigned warehouses.",
                403,
            )
        if location.usage != "internal":
            return error(
                "INVALID_LOCATION",
                "Destination must be an internal stock location.",
                400,
            )

    # Odoo 17 stock.move._split returns copy_data vals (list[dict]), not an id.
    original = move
    targets = []
    for location, qty in destinations[:-1]:
        if qty <= 0:
            continue
        try:
            new_move_vals = original._split(qty)
        except (UserError, ValidationError) as exc:
            return native_error("MOVE_SPLIT_FAILED", exc)
        if not new_move_vals:
            continue
        try:
            target = original.env["stock.move"].sudo().create(new_move_vals)
        except (UserError, ValidationError) as exc:
            return native_error("MOVE_SPLIT_FAILED", exc)
        if not target:
            return error("MOVE_SPLIT_FAILED", "Could not split stock move.", 400)
        # Keep split moves on the same picking / confirm for qty edits.
        if target.state == "draft":
            target._action_confirm(merge=False)
        targets.append((target, location, qty))
    last_location, last_qty = destinations[-1]
    targets.append((original, last_location, last_qty))

    for target, location, qty in targets:
        target.write({"location_dest_id": location.id})
        for line in target.move_line_ids.filtered(
            lambda row: row.state not in ("done", "cancel")
        ):
            line.write({"location_dest_id": location.id})
        try:
            set_move_done_qty(target, qty)
        except (UserError, ValidationError) as exc:
            return native_error("INVALID_QUANTITY", exc)
    return None


def parse_receive_line_destinations(env, line, move, allowed_locations):
    """Normalize receive line into destinations list."""
    destinations_raw = line.get("destinations")
    if destinations_raw is not None:
        if not isinstance(destinations_raw, list) or not destinations_raw:
            return None, error(
                "INVALID_FIELDS",
                "destinations must be a non-empty array.",
                400,
            )
        destinations = []
        for row in destinations_raw:
            if not isinstance(row, dict):
                return None, error("INVALID_FIELDS", "Each destination must be an object.", 400)
            location_id = strict_int(row.get("location_id"))
            qty = strict_float(row.get("quantity"))
            if not location_id or qty is None:
                return None, error(
                    "INVALID_FIELDS",
                    "Each destination needs location_id and quantity.",
                    400,
                )
            location = env["stock.location"].sudo().browse(location_id).exists()
            if not location:
                return None, error("LOCATION_NOT_FOUND", "Location not found.", 404)
            destinations.append((location, qty))
        return destinations, None

    qty = strict_float(line.get("quantity_done"))
    if qty is None:
        return None, error(
            "INVALID_RECEIPT_QUANTITY",
            "quantity_done must be a number >= 0.",
            400,
        )
    location_id = strict_int(line.get("location_dest_id"))
    if location_id:
        location = env["stock.location"].sudo().browse(location_id).exists()
        if not location:
            return None, error("LOCATION_NOT_FOUND", "Location not found.", 404)
    else:
        location = move.location_dest_id
    return [(location, qty)], None
