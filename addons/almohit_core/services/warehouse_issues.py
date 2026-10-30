"""Sales stock issues — warehouse dispatch without touching distributor user_id."""
from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_date_range_params, parse_pagination, success, _query_scalar
from .payments import order_allows_distribution
from .warehouse_common import (
    assignment_or_error,
    allowed_location_ids,
    ensure_confirmed_assigned,
    finish_validate,
    location_in_warehouses,
    move_done_qty,
    named,
    native_error,
    require_perm,
    set_move_done_qty,
    strict_float,
    strict_int,
    sudo_picking,
    warehouse_for_location,
)


def _sale_of_picking(picking):
    sale = picking.sale_id
    if sale:
        return sale
    if picking.origin:
        return picking.env["sale.order"].sudo().search(
            [("name", "=", picking.origin), ("company_id", "=", picking.company_id.id)],
            limit=1,
        )
    return picking.env["sale.order"]


def _issue_scope_domain(assignment):
    location_ids = allowed_location_ids(assignment.warehouse_ids)
    if not location_ids:
        return [("id", "=", 0)]
    return [
        ("picking_type_code", "=", "outgoing"),
        ("company_id", "=", assignment.env.company.id),
        ("location_id", "in", location_ids),
        ("almohit_storefront_delivery", "=", True),
    ]


def _serialize_issue_line(move):
    ordered = float(move.product_uom_qty or 0.0)
    reserved = float(move.reserved_availability or 0.0) if "reserved_availability" in move._fields else 0.0
    done = move_done_qty(move)
    if move.state == "done" and done <= 0:
        done = ordered
    return {
        "move_id": move.id,
        "source_location": named(move.location_id),
        "destination_location": named(move.location_dest_id),
        "product": {
            "id": move.product_id.id,
            "name": move.product_id.display_name,
            "default_code": move.product_id.default_code or None,
        },
        "ordered_qty": ordered,
        "reserved_qty": reserved,
        "done_qty": done,
        "remaining_qty": max(ordered - done, 0.0),
        "uom": named(move.product_uom),
    }


def serialize_issue(picking, detail=False):
    sale = _sale_of_picking(picking)
    warehouses = picking.env["stock.warehouse"].sudo().search(
        [("company_id", "=", picking.company_id.id)]
    )
    item = {
        "id": picking.id,
        "name": picking.name,
        "state": picking.state,
        "dispatch_state": picking.almohit_dispatch_state or "pending",
        "scheduled_date": picking.scheduled_date or None,
        "date_done": picking.date_done or None,
        "customer": named(picking.partner_id),
        "sale_order": named(sale) if sale else None,
        "origin": picking.origin or None,
        "warehouse": named(warehouse_for_location(picking.location_id, warehouses)),
        "distributor_user_id": picking.user_id.id if picking.user_id else None,
        "payment_ok": bool(sale and order_allows_distribution(sale)) if sale else True,
    }
    if detail:
        moves = picking.move_ids.filtered(lambda move: not move.scrapped)
        item["lines"] = [_serialize_issue_line(move) for move in moves]
        item["dispatch_picking_id"] = (
            picking.almohit_dispatch_picking_id.id
            if picking.almohit_dispatch_picking_id
            else None
        )
    return item


def count_pending_issues(assignment):
    domain = _issue_scope_domain(assignment) + [
        ("state", "not in", ("done", "cancel")),
        ("almohit_dispatch_state", "in", (False, "pending")),
    ]
    return assignment.env["stock.picking"].sudo().search_count(domain)


def list_issues(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_issue", "issue")
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err

    domain = _issue_scope_domain(assignment)
    status = (_query_scalar(kwargs, "status") or "pending").strip().lower()
    if status == "pending":
        domain += [
            ("state", "not in", ("done", "cancel")),
            ("almohit_dispatch_state", "in", (False, "pending")),
        ]
    elif status == "dispatched":
        domain.append(("almohit_dispatch_state", "=", "dispatched"))
    elif status == "done":
        domain.append(("state", "=", "done"))
    elif status == "all":
        pass
    else:
        return error(
            "INVALID_FILTER",
            "status must be pending, dispatched, done, or all.",
            400,
        )

    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("scheduled_date", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("scheduled_date", "<=", datetime.combine(date_to, time(23, 59, 59))))

    q = (_query_scalar(kwargs, "q") or "").strip()
    if q:
        domain += [
            "|",
            "|",
            ("name", "ilike", q),
            ("origin", "ilike", q),
            ("partner_id.name", "ilike", q),
        ]

    pickings = partner.env["stock.picking"].sudo().search(domain, order="id desc")
    return paginated_success(pickings, serialize_issue, page, page_size)


def get_issue(partner, picking_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_issue", "issue")
    if err:
        return err
    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "outgoing":
        return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)
    if not location_in_warehouses(picking.location_id, assignment.warehouse_ids):
        return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)
    return success(serialize_issue(picking, detail=True))


def _output_location(warehouse):
    """Prefer a distinct output/pack location so dispatch ≠ customer delivery.

    Falling back to lot_stock would make stock→stock a no-op and risk double
    deduction when the distributor later validates the outgoing delivery.
    """
    output = warehouse.wh_output_stock_loc_id
    if output and output != warehouse.lot_stock_id:
        return output
    packing = getattr(warehouse, "wh_pack_stock_loc_id", False)
    if packing and packing != warehouse.lot_stock_id:
        return packing
    return warehouse.env["stock.location"]


def dispatch_issue(partner, picking_id, payload):
    """Move goods stock→output without validating the customer delivery picking.

    Creates/validates an internal dispatch picking. Leaves the sale delivery
    open for the distributor (`user_id` untouched; delivery not marked done).
    """
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_issue", "issue")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)
    extra = set(payload) - {"lines"}
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)

    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "outgoing":
        return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)
    # State/idempotency before location scope so retries after stock→output
    # relocation return stable 409 codes instead of 404.
    if picking.state in ("done", "cancel"):
        return error("ISSUE_ALREADY_DONE", "Customer delivery is already closed.", 409)
    if picking.almohit_dispatch_state == "dispatched":
        return error("ISSUE_ALREADY_DISPATCHED", "Already dispatched from warehouse.", 409)
    if not location_in_warehouses(picking.location_id, assignment.warehouse_ids):
        return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)

    sale = _sale_of_picking(picking)
    if sale and sale.almohit_storefront_order and not order_allows_distribution(sale):
        return error(
            "PAYMENT_REQUIRED",
            "Current due amount must be settled before warehouse dispatch.",
            409,
        )

    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return error("INVALID_FIELDS", "lines must be a non-empty array.", 400)

    warehouses = assignment.warehouse_ids
    warehouse = warehouse_for_location(picking.location_id, warehouses)
    if not warehouse:
        return error("WAREHOUSE_NOT_ALLOWED", "Source warehouse is not assigned.", 403)
    output = _output_location(warehouse)
    if not output:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse needs a distinct Output (or Pack) location for dispatch "
            "so distributor delivery is not duplicated on the same stock location.",
            400,
        )
    if warehouse.company_id and warehouse.company_id != partner.env.company:
        return error("CROSS_COMPANY_DENIED", "Resource belongs to another company.", 403)
    allowed_src = set(allowed_location_ids(warehouses))

    # Confirm/assign customer picking for reservation visibility only.
    customer_picking = sudo_picking(picking)
    try:
        ensure_confirmed_assigned(customer_picking)
    except (UserError, ValidationError) as exc:
        return native_error("ISSUE_NOT_READY", exc)

    moves = {
        move.id: move
        for move in customer_picking.move_ids
        if move.state not in ("done", "cancel")
    }
    Picking = partner.env["stock.picking"].sudo()
    Move = partner.env["stock.move"].sudo()
    internal_type = warehouse.int_type_id
    if not internal_type:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse has no internal transfer operation type.",
            400,
        )

    dispatch_vals = {
        "picking_type_id": internal_type.id,
        "location_id": warehouse.lot_stock_id.id,
        "location_dest_id": output.id,
        "origin": "WH-DISPATCH/%s" % customer_picking.name,
        "partner_id": customer_picking.partner_id.id,
        "company_id": customer_picking.company_id.id,
        "move_ids": [],
    }
    move_commands = []
    seen = set()
    for line in lines:
        if not isinstance(line, dict):
            return error("INVALID_FIELDS", "Each issue line must be an object.", 400)
        extra_line = set(line) - {"move_id", "quantity_done", "location_id"}
        if extra_line:
            return error("INVALID_FIELDS", "One or more line fields are not allowed.", 400)
        move_id = strict_int(line.get("move_id"))
        qty = strict_float(line.get("quantity_done"))
        if not move_id or move_id in seen or qty is None or qty < 0:
            return error("INVALID_FIELDS", "Invalid move_id or quantity_done.", 400)
        seen.add(move_id)
        move = moves.get(move_id)
        if not move:
            return error("ISSUE_LINE_NOT_FOUND", "Issue line not found.", 404)
        source_id = strict_int(line.get("location_id")) or move.location_id.id
        if source_id not in allowed_src:
            return error("WAREHOUSE_NOT_ALLOWED", "Source location not allowed.", 403)
        if qty > float(move.product_uom_qty or 0.0) + 1e-6:
            return error(
                "INVALID_QUANTITY",
                "quantity_done cannot exceed ordered quantity.",
                400,
            )
        # Availability check
        Quant = partner.env["stock.quant"].sudo()
        available = Quant._get_available_quantity(
            move.product_id,
            partner.env["stock.location"].browse(source_id),
        ) if hasattr(Quant, "_get_available_quantity") else None
        if available is not None and qty > available + 1e-6:
            return error(
                "INSUFFICIENT_STOCK",
                "Insufficient free stock for %s." % move.product_id.display_name,
                409,
            )
        move_commands.append((0, 0, {
            "name": move.product_id.display_name,
            "product_id": move.product_id.id,
            "product_uom_qty": qty,
            "product_uom": move.product_uom.id,
            "location_id": source_id,
            "location_dest_id": output.id,
            "company_id": customer_picking.company_id.id,
        }))

    if not move_commands:
        return error("INVALID_FIELDS", "No dispatch lines provided.", 400)

    dispatch_vals["move_ids"] = move_commands
    try:
        dispatch = Picking.with_context(skip_almohit_payment_assign=True).create(dispatch_vals)
        dispatch = sudo_picking(dispatch)
        ensure_confirmed_assigned(dispatch)
        for move in dispatch.move_ids:
            set_move_done_qty(move, float(move.product_uom_qty or 0.0))
        result = dispatch.button_validate()
        finish_validate(dispatch, result)
    except (UserError, ValidationError) as exc:
        return native_error("ISSUE_DISPATCH_FAILED", exc)

    # Relocate customer delivery source to output for distributor pickup.
    # Do NOT validate customer delivery and do NOT touch user_id.
    try:
        customer_picking.with_context(skip_almohit_payment_assign=True).write({
            "location_id": output.id,
            "almohit_dispatch_state": "dispatched",
            "almohit_dispatch_picking_id": dispatch.id,
        })
        for move in customer_picking.move_ids.filtered(lambda row: row.state not in ("done", "cancel")):
            move.write({"location_id": output.id})
            for ml in move.move_line_ids:
                ml.write({"location_id": output.id})
    except (UserError, ValidationError) as exc:
        return native_error("ISSUE_DISPATCH_FAILED", exc)

    customer_picking.invalidate_recordset()
    return success(serialize_issue(customer_picking, detail=True))
