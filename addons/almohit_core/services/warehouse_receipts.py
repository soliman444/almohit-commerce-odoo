from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_date_range_params, parse_pagination, success, _query_scalar
from .customer import serialize_warehouse_access
from .warehouse_common import (
    apply_move_destinations,
    assignment_or_error,
    ensure_confirmed_assigned,
    finish_validate,
    location_in_warehouses,
    named,
    native_error,
    parse_receive_line_destinations,
    receipt_validation_mode,
    require_perm,
    strict_int,
    sudo_picking,
    allowed_location_ids,
    warehouse_for_location,
    move_done_qty,
)


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


def _serialize_receipt_line(move):
    ordered = float(move.product_uom_qty or 0.0)
    received = move_done_qty(move)
    if move.state == "done" and received <= 0:
        received = ordered
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
        "received_qty": received,
        "remaining_qty": max(ordered - received, 0.0),
        "uom": named(move.product_uom),
    }


def serialize_receipt(picking, detail=False):
    purchase = _purchase_of_picking(picking)
    is_return = _is_customer_return(picking)
    returned_from = None
    if is_return and getattr(picking, "return_id", False):
        returned_from = named(picking.return_id)
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
        "warehouse": named(
            warehouse_for_location(picking.location_dest_id, picking.env["stock.warehouse"].browse())
        ),
    }
    # Prefer matching warehouse via company warehouses containing dest.
    warehouses = picking.env["stock.warehouse"].sudo().search(
        [("company_id", "=", picking.company_id.id)]
    )
    item["warehouse"] = named(warehouse_for_location(picking.location_dest_id, warehouses))
    if detail:
        moves = picking.move_ids.filtered(lambda move: not move.scrapped)
        item["lines"] = [_serialize_receipt_line(move) for move in moves]
        item["backorder_id"] = picking.backorder_id.id if picking.backorder_id else None
        backorders = picking.backorder_ids if "backorder_ids" in picking._fields else picking.env["stock.picking"]
        item["backorders"] = [named(row) for row in backorders]
        # Prices intentionally omitted for warehouse managers.
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
        pickings = pickings.filtered(lambda picking: bool(_purchase_of_picking(picking)) and not _is_customer_return(picking))
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
        # Dest may still be WH input; also allow if any move dest is allowed.
        allowed = allowed_location_ids(assignment.warehouse_ids)
        move_ok = any(move.location_dest_id.id in allowed for move in picking.move_ids)
        if not move_ok:
            return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    return success(serialize_receipt(picking, detail=True))


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
    extra = set(payload) - {"lines"}
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return error("INVALID_FIELDS", "lines must be a non-empty array.", 400)

    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "incoming":
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    allowed = set(allowed_location_ids(assignment.warehouse_ids))
    if picking.location_dest_id.id not in allowed and not any(
        move.location_dest_id.id in allowed for move in picking.move_ids
    ):
        return error("RECEIPT_NOT_FOUND", "Receipt not found.", 404)
    if picking.state in ("done", "cancel"):
        return error("RECEIPT_ALREADY_DONE", "This receipt was already processed.", 409)

    picking = sudo_picking(picking)
    try:
        ensure_confirmed_assigned(picking)
    except (UserError, ValidationError) as exc:
        return native_error("RECEIPT_NOT_READY", exc)

    moves = {move.id: move for move in picking.move_ids if move.state not in ("done", "cancel")}
    seen = set()
    for line in lines:
        if not isinstance(line, dict):
            return error("INVALID_FIELDS", "Each receipt line must be an object.", 400)
        extra_line = set(line) - {
            "move_id",
            "quantity_done",
            "location_dest_id",
            "destinations",
        }
        if extra_line:
            return error("INVALID_FIELDS", "One or more line fields are not allowed.", 400)
        move_id = strict_int(line.get("move_id"))
        if not move_id or move_id in seen:
            return error(
                "INVALID_FIELDS",
                "Each move_id must be unique and valid.",
                400,
            )
        seen.add(move_id)
        move = moves.get(move_id)
        if not move:
            return error("RECEIPT_LINE_NOT_FOUND", "Receipt line not found.", 404)
        destinations, derr = parse_receive_line_destinations(
            partner.env, line, move, allowed
        )
        if derr:
            return derr
        split_err = apply_move_destinations(move, destinations, allowed)
        if split_err:
            return split_err

    try:
        result = picking.button_validate()
        finish_validate(picking, result)
    except (UserError, ValidationError) as exc:
        return native_error("RECEIPT_VALIDATE_FAILED", exc)

    picking.invalidate_recordset()
    data = serialize_receipt(picking, detail=True)
    return success(data)
