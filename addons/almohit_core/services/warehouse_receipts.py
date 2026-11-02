"""Warehouse manager purchase/return receipts.

Receive contract: every line must send move_id + quantity_done + warehouse_id.
Different warehouses in one request are split onto native incoming pickings
(per warehouse operation type) before validate — never by mixing destinations
inside a single picking_type.
"""
from collections import defaultdict
from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_date_range_params, parse_pagination, success, _query_scalar
from .warehouse_common import (
    assignment_or_error,
    ensure_confirmed_assigned,
    filter_assignment_warehouses,
    finish_validate,
    location_in_warehouses,
    named,
    native_error,
    receipt_validation_mode,
    require_perm,
    set_move_done_qty,
    strict_float,
    strict_int,
    sudo_picking,
    allowed_location_ids,
    warehouse_for_location,
    move_posted_qty,
    move_prepared_qty,
    move_reserved_qty,
)


class _ReceiptAbort(Exception):
    def __init__(self, response):
        super().__init__("receipt abort")
        self.response = response


def _purchase_of_picking(picking):
    if "purchase_id" in picking._fields and picking.purchase_id:
        return picking.purchase_id
    orders = picking.move_ids.mapped("purchase_line_id.order_id")
    return orders[:1]


def _is_customer_return(picking):
    if picking.picking_type_code != "incoming":
        return False
    if getattr(picking, "return_id", False):
        return True
    if picking.move_ids.filtered(lambda move: bool(getattr(move, "origin_returned_move_id", False))):
        return True
    origin = (picking.origin or "").upper()
    return origin.startswith("RET") or "RETURN" in origin


def _receipt_scope_domain(assignment):
    warehouses = assignment.warehouse_ids
    location_ids = allowed_location_ids(warehouses)
    if not location_ids:
        return [("id", "=", 0)]
    return [
        ("picking_type_code", "=", "incoming"),
        ("company_id", "=", assignment.env.company.id),
        ("location_dest_id", "in", location_ids),
    ]


def _serialize_receipt_line(move, company_warehouses=None):
    ordered = float(move.product_uom_qty or 0.0)
    received = move_posted_qty(move)
    prepared = move_prepared_qty(move)
    reserved = move_reserved_qty(move)
    company_warehouses = company_warehouses or move.env["stock.warehouse"]
    warehouse = warehouse_for_location(move.location_dest_id, company_warehouses)
    if not warehouse and move.picking_id and move.picking_id.picking_type_id:
        warehouse = move.picking_id.picking_type_id.warehouse_id
    return {
        "move_id": move.id,
        "source_location": named(move.location_id),
        "destination_location": named(move.location_dest_id),
        "warehouse": named(warehouse) if warehouse else None,
        "product": {
            "id": move.product_id.id,
            "name": move.product_id.display_name,
            "default_code": move.product_id.default_code or None,
        },
        "ordered_qty": ordered,
        "prepared_qty": prepared,
        "reserved_qty": reserved,
        "received_qty": received,
        "remaining_qty": max(ordered - received, 0.0),
        "uom": named(move.product_uom),
    }


def _suggested_receipt_warehouse(picking, purchase, company_warehouses):
    """Informational only — receive uses per-line warehouse_id."""
    if purchase and purchase.picking_type_id and purchase.picking_type_id.warehouse_id:
        return purchase.picking_type_id.warehouse_id
    type_wh = picking.picking_type_id.warehouse_id if picking.picking_type_id else False
    if type_wh:
        return type_wh
    return warehouse_for_location(picking.location_dest_id, company_warehouses)


def _receipt_putaway_location(warehouse):
    """Native incoming destination for a warehouse (Input if configured, else Stock)."""
    in_type = warehouse.in_type_id
    if in_type and in_type.default_location_dest_id:
        return in_type.default_location_dest_id
    input_loc = warehouse.wh_input_stock_loc_id
    if input_loc:
        return input_loc
    return warehouse.lot_stock_id


def _active_receipt_moves(picking):
    return picking.move_ids.filtered(
        lambda move: not move.scrapped and move.state != "cancel"
    )


def serialize_receipt(picking, detail=False, assignment=None, result_pickings=None):
    purchase = _purchase_of_picking(picking)
    is_return = _is_customer_return(picking)
    returned_from = None
    if is_return and getattr(picking, "return_id", False):
        returned_from = named(picking.return_id)
    company_warehouses = picking.env["stock.warehouse"].sudo().search([
        ("company_id", "=", picking.company_id.id),
        ("active", "=", True),
    ])
    current = warehouse_for_location(picking.location_dest_id, company_warehouses)
    if not current and picking.picking_type_id:
        current = picking.picking_type_id.warehouse_id
    suggested = _suggested_receipt_warehouse(picking, purchase, company_warehouses)
    item = {
        "id": picking.id,
        "name": picking.name,
        "state": picking.state,
        "scheduled_date": picking.scheduled_date or None,
        "date_done": picking.date_done or None,
        "partner": named(picking.partner_id),
        "purchase": named(purchase) if purchase else None,
        "is_return": is_return,
        "returned_from": returned_from,
        "origin": picking.origin or None,
        "warehouse": named(current),
    }
    if detail:
        moves = picking.move_ids.filtered(lambda move: not move.scrapped)
        item["lines"] = [
            _serialize_receipt_line(move, company_warehouses) for move in moves
        ]
        item["backorder_id"] = picking.backorder_id.id if picking.backorder_id else None
        backorders = (
            picking.backorder_ids
            if "backorder_ids" in picking._fields
            else picking.env["stock.picking"]
        )
        item["backorders"] = [named(row) for row in backorders]
        # Informational only — receive requires warehouse_id on each POST line.
        item["suggested_warehouse"] = named(suggested)
        item["current_warehouse"] = named(current)
        item["available_warehouses"] = []
        item["receive_hint"] = (
            "Choose warehouse_id per line on POST .../receive from available_warehouses "
            "or GET /warehouse/my-warehouses. current/suggested are not required."
        )
        if assignment:
            assigned = filter_assignment_warehouses(assignment, picking.company_id)
            item["available_warehouses"] = [
                {"id": warehouse.id, "name": warehouse.display_name or warehouse.name or ""}
                for warehouse in assigned.sorted(key=lambda row: (row.name or "", row.id))
            ]
        if result_pickings is not None:
            item["result_pickings"] = []
            for row in result_pickings:
                row_wh = warehouse_for_location(row.location_dest_id, company_warehouses)
                if not row_wh and row.picking_type_id:
                    row_wh = row.picking_type_id.warehouse_id
                item["result_pickings"].append({
                    "id": row.id,
                    "name": row.name,
                    "state": row.state,
                    "warehouse": named(row_wh) if row_wh else None,
                    "backorder_of": row.backorder_id.id if row.backorder_id else None,
                })
    return item


def count_pending_receipts(assignment):
    domain = _receipt_scope_domain(assignment) + [
        ("state", "not in", ("done", "cancel")),
    ]
    return assignment.env["stock.picking"].sudo().search_count(domain)


def list_receipts(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_receipt", "receipt")
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err

    domain = _receipt_scope_domain(assignment)
    raw_status = _query_scalar(kwargs, "status")
    if raw_status not in (None, ""):
        status = str(raw_status).strip().lower()
        if status == "pending":
            domain.append(("state", "not in", ("done", "cancel")))
        elif status == "ready":
            domain.append(("state", "=", "assigned"))
        elif status in ("draft", "waiting", "confirmed", "assigned", "done", "cancel"):
            domain.append(("state", "=", status))
        else:
            return error(
                "INVALID_FILTER",
                "status must be pending, ready, draft, waiting, confirmed, assigned, done, or cancel.",
                400,
            )
    else:
        domain.append(("state", "not in", ("done", "cancel")))

    kind = (_query_scalar(kwargs, "kind") or "").strip().lower()
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
    if kind == "purchase":
        pickings = pickings.filtered(
            lambda picking: bool(_purchase_of_picking(picking)) and not _is_customer_return(picking)
        )
    elif kind == "return":
        pickings = pickings.filtered(_is_customer_return)

    return paginated_success(
        pickings,
        lambda picking: serialize_receipt(picking),
        page,
        page_size,
    )


def get_receipt(partner, picking_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_receipt", "receipt")
    if err:
        return err
    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "incoming":
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    if not location_in_warehouses(picking.location_dest_id, assignment.warehouse_ids):
        allowed = allowed_location_ids(assignment.warehouse_ids)
        move_ok = any(move.location_dest_id.id in allowed for move in picking.move_ids)
        if not move_ok:
            return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    return success(serialize_receipt(picking, detail=True, assignment=assignment))


def _lock_receipt(picking):
    picking.env.cr.execute(
        "SELECT pg_try_advisory_xact_lock(%s, %s)",
        (87110424, int(picking.id)),
    )
    if not picking.env.cr.fetchone()[0]:
        return error(
            "RECEIPT_BUSY",
            "Receipt is being updated by another request. Retry shortly.",
            409,
        )
    picking.env.cr.execute(
        "SELECT id FROM stock_picking WHERE id = %s FOR UPDATE",
        (picking.id,),
    )
    return None


def _assert_warehouse_ready(warehouse):
    if not warehouse.in_type_id or not warehouse.lot_stock_id:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse is missing its incoming operation type or stock location.",
            400,
        )
    return None


def _retarget_picking_to_warehouse(picking, warehouse):
    """Point an open incoming picking at a warehouse operation type/destination."""
    dest_loc = _receipt_putaway_location(warehouse)
    in_type = warehouse.in_type_id
    current = warehouse_for_location(
        picking.location_dest_id,
        picking.env["stock.warehouse"].browse(warehouse.id),
    )
    type_wh = picking.picking_type_id.warehouse_id if picking.picking_type_id else False
    if (
        type_wh
        and type_wh.id == warehouse.id
        and picking.location_dest_id.id == dest_loc.id
    ):
        return picking

    receipt = sudo_picking(picking)
    if hasattr(receipt, "do_unreserve"):
        receipt.do_unreserve()
    moves = _active_receipt_moves(receipt)
    if moves.move_line_ids:
        moves.move_line_ids.unlink()
    moves._action_cancel()
    moves.write({"state": "draft"})
    if "is_locked" in receipt._fields:
        receipt.write({"is_locked": False})
    receipt.invalidate_recordset(["state"])
    receipt.write({
        "picking_type_id": in_type.id,
        "location_dest_id": dest_loc.id,
    })
    for move in moves:
        vals = {
            "state": "draft",
            "location_dest_id": dest_loc.id,
            "picking_type_id": in_type.id,
        }
        if "warehouse_id" in move._fields:
            vals["warehouse_id"] = warehouse.id
        move.write(vals)
    ensure_confirmed_assigned(receipt)
    return receipt


def _split_moves_to_warehouse_picking(source_picking, moves, warehouse):
    """Create a sibling incoming picking for moves destined to another warehouse.

    Keeps purchase_line_id / origin links on the moves (native PO receipt chain).
    """
    if not moves:
        return source_picking.env["stock.picking"]
    dest_loc = _receipt_putaway_location(warehouse)
    in_type = warehouse.in_type_id
    source = sudo_picking(source_picking)
    if hasattr(source, "do_unreserve"):
        source.do_unreserve()

    Move = moves.env["stock.move"].sudo()
    moves = Move.browse(moves.ids)
    if moves.move_line_ids:
        moves.move_line_ids.unlink()
    moves._action_cancel()
    moves.write({"state": "draft"})

    vals = {
        "picking_type_id": in_type.id,
        "location_id": source.location_id.id,
        "location_dest_id": dest_loc.id,
        "partner_id": source.partner_id.id,
        "origin": source.origin,
        "company_id": source.company_id.id,
        "scheduled_date": source.scheduled_date,
        "move_type": source.move_type,
    }
    if "owner_id" in source._fields:
        vals["owner_id"] = source.owner_id.id
    new_picking = source.env["stock.picking"].sudo().create(vals)
    new_picking = sudo_picking(new_picking)

    for move in moves:
        move_vals = {
            "picking_id": new_picking.id,
            "picking_type_id": in_type.id,
            "location_dest_id": dest_loc.id,
            "state": "draft",
        }
        if "warehouse_id" in move._fields:
            move_vals["warehouse_id"] = warehouse.id
        move.write(move_vals)

    ensure_confirmed_assigned(new_picking)
    return new_picking


def _validate_picking_quantities(picking, move_qty_map):
    """Set done quantities then validate. move_qty_map: {move_id: qty}."""
    picking = sudo_picking(picking)
    ensure_confirmed_assigned(picking)
    active = {
        move.id: move
        for move in picking.move_ids
        if move.state not in ("done", "cancel") and not move.scrapped
    }
    for move_id, qty in move_qty_map.items():
        move = active.get(move_id)
        if not move:
            raise UserError("Receipt line %s is not on picking %s." % (move_id, picking.name))
        set_move_done_qty(move, qty)
    # Moves not in this batch stay at 0 → Odoo backorder on validate.
    for move_id, move in active.items():
        if move_id not in move_qty_map:
            set_move_done_qty(move, 0.0)

    result = picking.button_validate()
    finish_validate(picking, result)
    picking.invalidate_recordset()
    return picking


def receive_receipt(partner, picking_id, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_receipt", "receipt")
    if err:
        return err
    mode = receipt_validation_mode(partner.env)
    if mode == "buyer_only":
        return error(
            "RECEIPT_BUYER_ONLY",
            "Receipt validation is currently assigned to buyers only.",
            403,
        )

    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)
    if "warehouse_id" in payload:
        return error(
            "INVALID_FIELDS",
            "warehouse_id must be set per line, not on the request body.",
            400,
        )
    if "location_id" in payload or "location_dest_id" in payload or "destinations" in payload:
        return error(
            "INVALID_FIELDS",
            "location_id, location_dest_id, and destinations are not accepted.",
            400,
        )
    extra = set(payload) - {"lines"}
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return error("INVALID_FIELDS", "lines must be a non-empty array.", 400)

    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "incoming":
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    assignment_locations = set(allowed_location_ids(assignment.warehouse_ids))
    if picking.location_dest_id.id not in assignment_locations and not any(
        move.location_dest_id.id in assignment_locations for move in picking.move_ids
    ):
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    if picking.state in ("done", "cancel"):
        return error("RECEIPT_ALREADY_DONE", "This receipt was already processed.", 409)

    lock_err = _lock_receipt(picking)
    if lock_err:
        return lock_err
    picking.invalidate_recordset()
    if picking.state in ("done", "cancel"):
        return error("RECEIPT_ALREADY_DONE", "This receipt was already processed.", 409)

    company_warehouses = filter_assignment_warehouses(assignment, picking.company_id)
    open_moves = {
        move.id: move
        for move in picking.move_ids
        if not move.scrapped
    }
    seen = set()
    parsed = []  # (move, qty, warehouse)
    for line in lines:
        if not isinstance(line, dict):
            return error("INVALID_FIELDS", "Each receipt line must be an object.", 400)
        if "location_id" in line or "location_dest_id" in line or "destinations" in line:
            return error(
                "INVALID_FIELDS",
                "location_id, location_dest_id, and destinations are not accepted.",
                400,
            )
        extra_line = set(line) - {"move_id", "quantity_done", "warehouse_id"}
        if extra_line:
            return error("INVALID_FIELDS", "One or more line fields are not allowed.", 400)

        move_id = strict_int(line.get("move_id"))
        warehouse_id = strict_int(line.get("warehouse_id"))
        qty = strict_float(line.get("quantity_done"))
        if not move_id or not warehouse_id or qty is None:
            return error(
                "INVALID_FIELDS",
                "Each line requires move_id, quantity_done, and warehouse_id.",
                400,
            )
        if move_id in seen:
            return error(
                "DUPLICATE_MOVE",
                "Duplicate move_id=%s in lines." % move_id,
                400,
            )
        seen.add(move_id)
        if qty <= 0 or qty != qty:
            return error(
                "INVALID_FIELDS",
                "quantity_done must be a positive number.",
                400,
            )

        move = open_moves.get(move_id)
        if not move or move.picking_id.id != picking.id:
            return error("RECEIPT_LINE_NOT_FOUND", "Receipt line not found.", 404)
        if move.state == "done":
            return error(
                "RECEIPT_LINE_ALREADY_DONE",
                "Move %s was already received; warehouse cannot be changed." % move_id,
                409,
            )
        if move.state == "cancel":
            return error("RECEIPT_LINE_NOT_FOUND", "Receipt line not found.", 404)

        remaining = max(
            float(move.product_uom_qty or 0.0) - move_posted_qty(move),
            0.0,
        )
        if qty > remaining + 1e-6:
            return error(
                "INVALID_QUANTITY",
                "quantity_done cannot exceed remaining quantity for move %s." % move_id,
                400,
            )

        if warehouse_id not in company_warehouses.ids:
            return error(
                "WAREHOUSE_NOT_ALLOWED",
                "Warehouse %s is not assigned to this account." % warehouse_id,
                403,
            )
        warehouse = company_warehouses.browse(warehouse_id)
        if warehouse.company_id and warehouse.company_id != partner.env.company:
            return error("CROSS_COMPANY_DENIED", "Resource belongs to another company.", 403)
        cfg_err = _assert_warehouse_ready(warehouse)
        if cfg_err:
            return cfg_err
        parsed.append((move, qty, warehouse))

    # Group by warehouse — each group needs its own incoming operation type/picking.
    by_warehouse = defaultdict(list)
    for move, qty, warehouse in parsed:
        by_warehouse[warehouse.id].append((move, qty, warehouse))

    original_id = picking.id
    try:
        with partner.env.cr.savepoint():
            result_pickings = picking.env["stock.picking"]
            requested_ids = {move.id for move, _qty, _wh in parsed}
            leftover_moves = _active_receipt_moves(picking).filtered(
                lambda move: move.id not in requested_ids
            )

            current_wh = warehouse_for_location(picking.location_dest_id, company_warehouses)
            if not current_wh and picking.picking_type_id:
                current_wh = picking.picking_type_id.warehouse_id
            warehouse_ids = list(by_warehouse.keys())
            # Keep the original picking only when every remaining open move is
            # received into the same warehouse (no leftover lines to protect).
            can_reuse_original = (
                not leftover_moves
                and len(by_warehouse) == 1
                and current_wh
                and current_wh.id in by_warehouse
            )
            primary_wh_id = current_wh.id if can_reuse_original else None

            # Split other warehouses first; never retarget unrequested moves.
            ordered_wh_ids = (
                [wh_id for wh_id in warehouse_ids if wh_id != primary_wh_id]
                + ([primary_wh_id] if primary_wh_id else [])
            )
            prepared = []  # (target_picking, qty_map, warehouse)

            for wh_id in ordered_wh_ids:
                rows = by_warehouse[wh_id]
                warehouse = rows[0][2]
                moves = picking.env["stock.move"].browse([row[0].id for row in rows])
                qty_map = {row[0].id: row[1] for row in rows}

                if wh_id == primary_wh_id:
                    target = _retarget_picking_to_warehouse(picking, warehouse)
                else:
                    target = _split_moves_to_warehouse_picking(picking, moves, warehouse)

                refreshed = {
                    move.id: move
                    for move in target.move_ids
                    if move.id in qty_map
                }
                if set(refreshed) != set(qty_map):
                    raise _ReceiptAbort(
                        error(
                            "RECEIPT_SPLIT_FAILED",
                            "Failed to place all lines on warehouse %s picking."
                            % warehouse.display_name,
                            500,
                        )
                    )
                prepared.append((target, qty_map, warehouse))

            # Validate only after every warehouse picking is prepared.
            for target, qty_map, warehouse in prepared:
                # These pickings contain only requested moves — validate exactly.
                validated = _validate_picking_quantities(target, qty_map)
                for move_id, expected in qty_map.items():
                    move = partner.env["stock.move"].sudo().browse(move_id).exists()
                    if not move or move.state != "done":
                        raise _ReceiptAbort(
                            error(
                                "RECEIPT_INCOMPLETE",
                                "Not all requested quantity was posted for move %s."
                                % move_id,
                                409,
                            )
                        )
                    if move_posted_qty(move) + 1e-6 < expected:
                        raise _ReceiptAbort(
                            error(
                                "RECEIPT_INCOMPLETE",
                                "Not all requested quantity was posted for move %s."
                                % move_id,
                                409,
                            )
                        )
                    if not location_in_warehouses(move.location_dest_id, warehouse):
                        raise _ReceiptAbort(
                            error(
                                "RECEIPT_WAREHOUSE_MISMATCH",
                                "Posted stock for move %s is not in warehouse %s."
                                % (move_id, warehouse.display_name),
                                500,
                            )
                        )
                result_pickings |= validated

            # Keep unrequested lines on the original picking; cancel an empty shell.
            original = partner.env["stock.picking"].sudo().browse(original_id)
            if original.exists() and original.state not in ("done", "cancel"):
                if leftover_moves:
                    ensure_confirmed_assigned(sudo_picking(original))
                elif not can_reuse_original and not _active_receipt_moves(original):
                    try:
                        sudo_picking(original).action_cancel()
                    except (UserError, ValidationError):
                        # Empty shell without moves is harmless if cancel is blocked.
                        pass

            # Reload original for response envelope.
            original = partner.env["stock.picking"].sudo().browse(original_id)
            data = serialize_receipt(
                original,
                detail=True,
                assignment=assignment,
                result_pickings=result_pickings,
            )
            # Merge lines from sibling result pickings for a full picture.
            company_whs = company_warehouses
            merged_lines = []
            seen_moves = set()
            for row in result_pickings:
                for move in row.move_ids.filtered(lambda candidate: not candidate.scrapped):
                    if move.id in seen_moves:
                        continue
                    seen_moves.add(move.id)
                    merged_lines.append(_serialize_receipt_line(move, company_whs))
            if merged_lines:
                data["lines"] = merged_lines
            # Collect backorders created across the batch.
            backorders = result_pickings.mapped("backorder_ids")
            if backorders:
                data["backorders"] = [named(row) for row in backorders]
            return success(data)
    except _ReceiptAbort as abort:
        return abort.response
    except (UserError, ValidationError) as exc:
        return native_error("RECEIPT_VALIDATE_FAILED", exc)
