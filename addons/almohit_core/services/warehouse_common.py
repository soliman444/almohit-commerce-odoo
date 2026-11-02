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
    """Who may validate purchase incoming receipts.

    Default is warehouse_only: Buyer creates/confirms POs; Warehouse Manager
    alone posts stock. Legacy values both|buyer_only remain readable for ICP.
    """
    raw = (
        env["ir.config_parameter"]
        .sudo()
        .get_param(ICP_RECEIPT_MODE, "warehouse_only")
        or "warehouse_only"
    ).strip().lower()
    return raw if raw in RECEIPT_MODES else "warehouse_only"


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


def _location_subtree_ids(env, root):
    """Child locations of root, including archived Output/Input/Pack."""
    if not root:
        return []
    Location = env["stock.location"].with_context(active_test=False).sudo()
    return Location.search([("id", "child_of", root.id)]).ids


def warehouse_for_location(location, warehouses):
    """Resolve warehouse from Odoo location tree (includes archived WH locations)."""
    if not location:
        return warehouses.env["stock.warehouse"]
    for warehouse in warehouses:
        if not warehouse.view_location_id:
            continue
        if location.id in _location_subtree_ids(location.env, warehouse.view_location_id):
            return warehouse
    return warehouses.env["stock.warehouse"]


def stock_role_for_location(location, warehouse):
    """Classify a quant location from warehouse routing settings (not fixed names).

    Roles for warehouse stock API consumers:
    - storage: lot_stock subtree (sellable / dispatchable source)
    - dispatch_staging: Output subtree (stock→output staging; not free to re-dispatch)
    - pack: Pack zone subtree
    - input: Input subtree
    - other: remaining internal warehouse locations
    """
    if not location or not warehouse:
        return "other"
    env = location.env
    if location.id in _location_subtree_ids(env, warehouse.lot_stock_id):
        return "storage"
    output = getattr(warehouse, "wh_output_stock_loc_id", False)
    if output and location.id in _location_subtree_ids(env, output):
        return "dispatch_staging"
    packing = getattr(warehouse, "wh_pack_stock_loc_id", False)
    if packing and location.id in _location_subtree_ids(env, packing):
        return "pack"
    incoming = getattr(warehouse, "wh_input_stock_loc_id", False)
    if incoming and location.id in _location_subtree_ids(env, incoming):
        return "input"
    return "other"


def warehouse_location_map(warehouses):
    """location_id → warehouse, including archived children under view_location."""
    mapping = {}
    for warehouse in warehouses:
        if not warehouse.view_location_id:
            continue
        for location_id in _location_subtree_ids(warehouse.env, warehouse.view_location_id):
            mapping[location_id] = warehouse
    return mapping


def dispatchable_qty(product, warehouse):
    """Qty eligible for warehouse dispatch (stock→output source).

    Uses the same native scope as issue dispatch validation:
    stock.quant._get_available_quantity(product, warehouse.lot_stock_id, strict=False).

    Includes child shelves under lot_stock. Excludes sibling Output/Input/Pack
    under the warehouse view (even when archived). Does not use product.free_qty
    with warehouse context (that sums the whole view_location tree).
    """
    if not product or not warehouse or not warehouse.lot_stock_id:
        return 0.0
    Quant = product.env["stock.quant"].sudo()
    if not hasattr(Quant, "_get_available_quantity"):
        return 0.0
    qty = Quant._get_available_quantity(
        product,
        warehouse.lot_stock_id,
        strict=False,
    )
    return float(qty or 0.0)


def internal_locations(warehouses):
    Location = warehouses.env["stock.location"].with_context(active_test=False).sudo()
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
    """Raw Odoo move quantity field (may be prefilled before validate on v17)."""
    if "quantity" in move._fields:
        return float(move.quantity or 0.0)
    if "quantity_done" in move._fields:
        return float(move.quantity_done or 0.0)
    return float(sum(move.move_line_ids.mapped("qty_done") or [0.0]))


def move_prepared_qty(move):
    """Qty entered on the move UI / lines before stock is posted."""
    if move.state in ("done", "cancel"):
        return 0.0
    return move_done_qty(move)


def move_posted_qty(move):
    """Qty actually posted to stock (validate completed).

    Odoo 17 often prefills ``stock.move.quantity`` on open incoming/outgoing
    moves; that must not be reported as received/done until ``state == done``.
    Partial receipts that created a backorder leave the validated move in
    ``done`` with its own ``product_uom_qty`` / posted quantity.
    """
    if move.state != "done":
        return 0.0
    posted = move_done_qty(move)
    ordered = float(move.product_uom_qty or 0.0)
    if posted <= 0:
        return ordered
    return posted


def move_reserved_qty(move):
    if move.state in ("done", "cancel"):
        return 0.0
    if "reserved_availability" in move._fields:
        return float(move.reserved_availability or 0.0)
    return float(sum(move.move_line_ids.mapped("reserved_uom_qty") or [0.0])) if "reserved_uom_qty" in move.move_line_ids._fields else 0.0


def sudo_picking(picking):
    return picking.with_user(SUPERUSER_ID).sudo()


def ensure_confirmed_assigned(picking):
    if picking.state == "draft" and hasattr(picking, "action_confirm"):
        picking.action_confirm()
    if picking.state in ("confirmed", "waiting", "assigned") and hasattr(picking, "action_assign"):
        picking.action_assign()

