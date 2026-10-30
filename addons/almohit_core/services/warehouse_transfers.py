from datetime import datetime, time

from odoo import fields
from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_date_range_params, parse_pagination, success, _query_scalar
from .warehouse_common import (
    assert_same_company,
    assignment_or_error,
    allowed_location_ids,
    ensure_confirmed_assigned,
    filter_assignment_warehouses,
    finish_validate,
    named,
    native_error,
    require_perm,
    set_move_done_qty,
    strict_float,
    strict_int,
    sudo_picking,
)


def _transit_location(env, company):
    Location = env["stock.location"].sudo()
    transit = Location.search([
        ("usage", "=", "transit"),
        ("company_id", "in", [False, company.id]),
    ], limit=1)
    if transit:
        return transit
    parent = env.ref("stock.stock_location_locations", raise_if_not_found=False)
    return Location.create({
        "name": "Almohit Inter-Warehouse Transit",
        "usage": "transit",
        "location_id": parent.id if parent else False,
        "company_id": company.id,
    })


def serialize_transfer(transfer, detail=False):
    item = {
        "id": transfer.id,
        "name": transfer.name,
        "state": transfer.state,
        "source_warehouse": named(transfer.source_warehouse_id),
        "dest_warehouse": named(transfer.dest_warehouse_id),
        "requested_by": named(transfer.requested_by_id),
        "dispatched_by": named(transfer.dispatched_by_id),
        "received_by": named(transfer.received_by_id),
        "requested_at": transfer.requested_at or None,
        "dispatched_at": transfer.dispatched_at or None,
        "received_at": transfer.received_at or None,
        "note": transfer.note or None,
        "open_discrepancies": len(transfer.discrepancy_ids.filtered(lambda row: row.state == "open")),
    }
    if detail:
        item["lines"] = [{
            "id": line.id,
            "product": {
                "id": line.product_id.id,
                "name": line.product_id.display_name,
                "default_code": line.product_id.default_code or None,
            },
            "uom": named(line.product_uom_id),
            "qty_requested": float(line.qty_requested or 0.0),
            "qty_dispatched": float(line.qty_dispatched or 0.0),
            "qty_received": float(line.qty_received or 0.0),
            "source_location": named(line.source_location_id),
            "dest_location": named(line.dest_location_id),
        } for line in transfer.line_ids]
        item["discrepancies"] = [{
            "id": row.id,
            "product": named(row.product_id),
            "qty_expected": float(row.qty_expected or 0.0),
            "qty_received": float(row.qty_received or 0.0),
            "qty_difference": float(row.qty_difference or 0.0),
            "reason": row.reason,
            "state": row.state,
            "note": row.note or None,
        } for row in transfer.discrepancy_ids]
        item["dispatch_picking_id"] = transfer.dispatch_picking_id.id or None
        item["receive_picking_id"] = transfer.receive_picking_id.id or None
    return item


def count_incoming_transfers(assignment):
    return assignment.env["almohit.warehouse.transfer"].sudo().search_count([
        ("dest_warehouse_id", "in", assignment.warehouse_ids.ids),
        ("state", "in", ("dispatched", "partially_received", "discrepancy")),
    ])


def count_outgoing_transfers(assignment):
    return assignment.env["almohit.warehouse.transfer"].sudo().search_count([
        ("source_warehouse_id", "in", assignment.warehouse_ids.ids),
        ("state", "in", ("requested", "dispatched", "partially_received")),
    ])


def count_open_discrepancies(assignment):
    return assignment.env["almohit.warehouse.discrepancy"].sudo().search_count([
        ("state", "=", "open"),
        "|",
        ("transfer_id.source_warehouse_id", "in", assignment.warehouse_ids.ids),
        ("transfer_id.dest_warehouse_id", "in", assignment.warehouse_ids.ids),
    ])


def _transfer_domain(assignment, direction):
    domain = [("company_id", "=", assignment.env.company.id)]
    if direction == "incoming":
        domain.append(("dest_warehouse_id", "in", assignment.warehouse_ids.ids))
    elif direction == "outgoing":
        domain.append(("source_warehouse_id", "in", assignment.warehouse_ids.ids))
    else:
        domain += [
            "|",
            ("source_warehouse_id", "in", assignment.warehouse_ids.ids),
            ("dest_warehouse_id", "in", assignment.warehouse_ids.ids),
        ]
    return domain


def list_transfers(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err
    direction = (_query_scalar(kwargs, "direction") or "all").strip().lower()
    if direction not in ("all", "incoming", "outgoing"):
        return error("INVALID_FILTER", "direction must be all, incoming, or outgoing.", 400)
    domain = _transfer_domain(assignment, direction)
    status = (_query_scalar(kwargs, "status") or "").strip().lower()
    if status:
        domain.append(("state", "=", status))
    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("create_date", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("create_date", "<=", datetime.combine(date_to, time(23, 59, 59))))
    transfers = partner.env["almohit.warehouse.transfer"].sudo().search(domain, order="id desc")
    return paginated_success(transfers, serialize_transfer, page, page_size)


def get_transfer(partner, transfer_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    transfer = partner.env["almohit.warehouse.transfer"].sudo().browse(transfer_id).exists()
    if not transfer:
        return error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    allowed = set(assignment.warehouse_ids.ids)
    if (
        transfer.source_warehouse_id.id not in allowed
        and transfer.dest_warehouse_id.id not in allowed
    ):
        return error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    return success(serialize_transfer(transfer, detail=True))


def create_transfer(partner, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_transfer_create", "transfer_create")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)
    extra = set(payload) - {
        "source_warehouse_id",
        "dest_warehouse_id",
        "note",
        "lines",
    }
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)

    source_id = strict_int(payload.get("source_warehouse_id"))
    dest_id = strict_int(payload.get("dest_warehouse_id"))
    if not source_id or not dest_id or source_id == dest_id:
        return error("INVALID_FIELDS", "Distinct source_warehouse_id and dest_warehouse_id are required.", 400)
    company_warehouses = filter_assignment_warehouses(assignment)
    if source_id not in company_warehouses.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Source warehouse is not assigned.", 403)
    source = partner.env["stock.warehouse"].sudo().browse(source_id)
    dest = partner.env["stock.warehouse"].sudo().browse(dest_id).exists()
    if not dest:
        return error("WAREHOUSE_NOT_FOUND", "Destination warehouse not found.", 404)
    company_err = assert_same_company(dest, partner.env.company)
    if company_err:
        return company_err

    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return error("INVALID_FIELDS", "lines must be a non-empty array.", 400)

    allowed_src = set(allowed_location_ids(source))
    line_vals = []
    for row in lines:
        if not isinstance(row, dict):
            return error("INVALID_FIELDS", "Each transfer line must be an object.", 400)
        product_id = strict_int(row.get("product_id"))
        qty = strict_float(row.get("quantity"))
        uom_id = strict_int(row.get("uom_id"))
        location_id = strict_int(row.get("location_id"))
        if not product_id or qty is None or qty <= 0:
            return error("INVALID_FIELDS", "product_id and positive quantity are required.", 400)
        product = partner.env["product.product"].sudo().browse(product_id).exists()
        if not product:
            return error("PRODUCT_NOT_FOUND", "Product not found.", 404)
        uom = partner.env["uom.uom"].sudo().browse(uom_id).exists() if uom_id else product.uom_id
        location = (
            partner.env["stock.location"].sudo().browse(location_id).exists()
            if location_id
            else source.lot_stock_id
        )
        if location.id not in allowed_src:
            return error("WAREHOUSE_NOT_ALLOWED", "Source location not allowed.", 403)
        line_vals.append({
            "product_id": product.id,
            "product_uom_id": uom.id,
            "qty_requested": qty,
            "source_location_id": location.id,
            "dest_location_id": dest.lot_stock_id.id,
        })

    transfer = partner.env["almohit.warehouse.transfer"].sudo().create({
        "source_warehouse_id": source.id,
        "dest_warehouse_id": dest.id,
        "note": (payload.get("note") or "").strip() or False,
        "state": "requested",
        "requested_by_id": partner.env.user.id,
        "requested_at": fields.Datetime.now(),
        "line_ids": [(0, 0, vals) for vals in line_vals],
    })
    return success(serialize_transfer(transfer, detail=True), status=201)


def dispatch_transfer(partner, transfer_id, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_transfer_create", "transfer_create")
    if err:
        return err
    transfer = partner.env["almohit.warehouse.transfer"].sudo().browse(transfer_id).exists()
    if not transfer:
        return error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    if transfer.source_warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Source warehouse is not assigned.", 403)
    if transfer.state != "requested":
        return error("TRANSFER_INVALID_STATE", "Only requested transfers can be dispatched.", 409)
    if transfer.dispatch_picking_id:
        return error("TRANSFER_ALREADY_DISPATCHED", "Transfer already dispatched.", 409)

    payload = payload if isinstance(payload, dict) else {}
    qty_map = {}
    if payload.get("lines"):
        if not isinstance(payload["lines"], list):
            return error("INVALID_FIELDS", "lines must be an array.", 400)
        for row in payload["lines"]:
            line_id = strict_int(row.get("line_id"))
            qty = strict_float(row.get("quantity"))
            if not line_id or qty is None or qty < 0:
                return error("INVALID_FIELDS", "Invalid line_id or quantity.", 400)
            qty_map[line_id] = qty

    source = transfer.source_warehouse_id
    transit = _transit_location(partner.env, transfer.company_id)
    internal_type = source.int_type_id
    if not internal_type:
        return error("WAREHOUSE_CONFIG", "Missing internal operation type.", 400)

    move_commands = []
    for line in transfer.line_ids:
        qty = qty_map.get(line.id, float(line.qty_requested or 0.0))
        if qty <= 0:
            continue
        if qty > float(line.qty_requested or 0.0) + 1e-6:
            return error("INVALID_QUANTITY", "Cannot dispatch more than requested.", 400)
        Quant = partner.env["stock.quant"].sudo()
        available = Quant._get_available_quantity(
            line.product_id, line.source_location_id
        ) if hasattr(Quant, "_get_available_quantity") else None
        if available is not None and qty > available + 1e-6:
            return error("INSUFFICIENT_STOCK", "Insufficient stock to dispatch.", 409)
        move_commands.append((0, 0, {
            "name": line.product_id.display_name,
            "product_id": line.product_id.id,
            "product_uom_qty": qty,
            "product_uom": line.product_uom_id.id,
            "location_id": line.source_location_id.id,
            "location_dest_id": transit.id,
            "company_id": transfer.company_id.id,
        }))
        line.qty_dispatched = qty

    if not move_commands:
        return error("INVALID_FIELDS", "No quantities to dispatch.", 400)

    try:
        picking = partner.env["stock.picking"].sudo().with_context(
            skip_almohit_payment_assign=True
        ).create({
            "picking_type_id": internal_type.id,
            "location_id": source.lot_stock_id.id,
            "location_dest_id": transit.id,
            "origin": transfer.name,
            "company_id": transfer.company_id.id,
            "move_ids": move_commands,
        })
        picking = sudo_picking(picking)
        ensure_confirmed_assigned(picking)
        for move in picking.move_ids:
            set_move_done_qty(move, float(move.product_uom_qty or 0.0))
        result = picking.button_validate()
        finish_validate(picking, result)
    except (UserError, ValidationError) as exc:
        return native_error("TRANSFER_DISPATCH_FAILED", exc)

    for line, move in zip(
        transfer.line_ids.filtered(lambda row: row.qty_dispatched > 0),
        picking.move_ids,
    ):
        line.dispatch_move_id = move.id

    transfer.write({
        "state": "dispatched",
        "dispatch_picking_id": picking.id,
        "dispatched_by_id": partner.env.user.id,
        "dispatched_at": fields.Datetime.now(),
    })
    return success(serialize_transfer(transfer, detail=True))


def receive_transfer(partner, transfer_id, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_transfer_receive", "transfer_receive")
    if err:
        return err
    transfer = partner.env["almohit.warehouse.transfer"].sudo().browse(transfer_id).exists()
    if not transfer:
        return error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    if transfer.dest_warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Destination warehouse is not assigned.", 403)
    if transfer.state not in ("dispatched", "partially_received", "discrepancy"):
        return error("TRANSFER_INVALID_STATE", "Transfer is not awaiting receipt.", 409)
    if transfer.receive_picking_id and transfer.state == "received":
        return error("TRANSFER_ALREADY_RECEIVED", "Transfer already received.", 409)

    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        return error("INVALID_FIELDS", "lines array is required.", 400)

    transit = _transit_location(partner.env, transfer.company_id)
    dest = transfer.dest_warehouse_id
    internal_type = dest.int_type_id
    if not internal_type:
        return error("WAREHOUSE_CONFIG", "Missing internal operation type.", 400)

    allowed_dest = set(allowed_location_ids(dest))
    line_by_id = {line.id: line for line in transfer.line_ids}
    receive_plan = []
    for row in payload["lines"]:
        line_id = strict_int(row.get("line_id"))
        qty = strict_float(row.get("quantity"))
        location_id = strict_int(row.get("location_id"))
        reason = (row.get("reason") or "missing").strip().lower()
        if not line_id or qty is None or qty < 0:
            return error("INVALID_FIELDS", "Invalid line_id or quantity.", 400)
        line = line_by_id.get(line_id)
        if not line:
            return error("TRANSFER_LINE_NOT_FOUND", "Transfer line not found.", 404)
        remaining = float(line.qty_dispatched or 0.0) - float(line.qty_received or 0.0)
        if qty > remaining + 1e-6:
            return error(
                "INVALID_QUANTITY",
                "Cannot receive more than remaining dispatched quantity.",
                409,
            )
        location = (
            partner.env["stock.location"].sudo().browse(location_id).exists()
            if location_id
            else dest.lot_stock_id
        )
        if location.id not in allowed_dest:
            return error("WAREHOUSE_NOT_ALLOWED", "Destination location not allowed.", 403)
        receive_plan.append((line, qty, location, reason))

    move_commands = []
    for line, qty, location, _reason in receive_plan:
        if qty <= 0:
            continue
        move_commands.append((0, 0, {
            "name": line.product_id.display_name,
            "product_id": line.product_id.id,
            "product_uom_qty": qty,
            "product_uom": line.product_uom_id.id,
            "location_id": transit.id,
            "location_dest_id": location.id,
            "company_id": transfer.company_id.id,
        }))

    if move_commands:
        try:
            picking = partner.env["stock.picking"].sudo().with_context(
                skip_almohit_payment_assign=True
            ).create({
                "picking_type_id": internal_type.id,
                "location_id": transit.id,
                "location_dest_id": dest.lot_stock_id.id,
                "origin": transfer.name,
                "company_id": transfer.company_id.id,
                "move_ids": move_commands,
            })
            picking = sudo_picking(picking)
            ensure_confirmed_assigned(picking)
            for move in picking.move_ids:
                set_move_done_qty(move, float(move.product_uom_qty or 0.0))
            result = picking.button_validate()
            finish_validate(picking, result)
            transfer.receive_picking_id = picking.id
        except (UserError, ValidationError) as exc:
            return native_error("TRANSFER_RECEIVE_FAILED", exc)

    Discrepancy = partner.env["almohit.warehouse.discrepancy"].sudo()
    for line, qty, location, reason in receive_plan:
        line.qty_received = float(line.qty_received or 0.0) + qty
        line.dest_location_id = location.id
        expected = float(line.qty_dispatched or 0.0)
        received = float(line.qty_received or 0.0)
        diff = received - expected
        if abs(diff) > 1e-6:
            Discrepancy.create({
                "transfer_id": transfer.id,
                "line_id": line.id,
                "product_id": line.product_id.id,
                "qty_expected": expected,
                "qty_received": received,
                "qty_difference": diff,
                "reason": reason if reason in ("missing", "excess", "damaged", "rejected", "other") else "missing",
                "state": "open",
            })

    all_received = all(
        float(line.qty_received or 0.0) + 1e-6 >= float(line.qty_dispatched or 0.0)
        for line in transfer.line_ids
        if float(line.qty_dispatched or 0.0) > 0
    )
    has_open = bool(transfer.discrepancy_ids.filtered(lambda row: row.state == "open"))
    if has_open:
        state = "discrepancy"
    elif all_received:
        state = "received"
    else:
        state = "partially_received"

    transfer.write({
        "state": state,
        "received_by_id": partner.env.user.id,
        "received_at": fields.Datetime.now(),
    })
    return success(serialize_transfer(transfer, detail=True))


def _accessible_transfer(partner, transfer_id, assignment):
    transfer = partner.env["almohit.warehouse.transfer"].sudo().browse(transfer_id).exists()
    if not transfer:
        return None, error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    allowed = set(assignment.warehouse_ids.ids)
    if (
        transfer.source_warehouse_id.id not in allowed
        and transfer.dest_warehouse_id.id not in allowed
    ):
        return None, error("TRANSFER_NOT_FOUND", "Transfer not found.", 404)
    if transfer.company_id and transfer.company_id != partner.env.company:
        return None, error("CROSS_COMPANY_DENIED", "Resource belongs to another company.", 403)
    return transfer, None


def cancel_transfer(partner, transfer_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    transfer, err = _accessible_transfer(partner, transfer_id, assignment)
    if err:
        return err
    if transfer.source_warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Only the source warehouse can cancel.", 403)
    err = require_perm(assignment, "perm_transfer_create", "transfer_create")
    if err:
        return err
    if transfer.state not in ("draft", "requested"):
        return error(
            "TRANSFER_INVALID_STATE",
            "Only draft/requested transfers can be cancelled.",
            409,
        )
    if transfer.dispatch_picking_id and transfer.dispatch_picking_id.state == "done":
        return error(
            "TRANSFER_INVALID_STATE",
            "Dispatched stock must be received or reconciled before cancel.",
            409,
        )
    transfer.write({"state": "cancelled", "note": _merge_note(transfer, payload, "cancelled")})
    return success(serialize_transfer(transfer, detail=True))


def reject_transfer(partner, transfer_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    transfer, err = _accessible_transfer(partner, transfer_id, assignment)
    if err:
        return err
    if transfer.dest_warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Only the destination warehouse can reject.", 403)
    err = require_perm(assignment, "perm_transfer_receive", "transfer_receive")
    if err:
        return err
    if transfer.state not in ("requested", "dispatched", "partially_received", "discrepancy"):
        return error(
            "TRANSFER_INVALID_STATE",
            "Transfer cannot be rejected in its current state.",
            409,
        )
    if transfer.state in ("dispatched", "partially_received", "discrepancy"):
        # Stock already left source — keep discrepancy workflow instead of silent reject.
        if transfer.state != "discrepancy":
            transfer.state = "discrepancy"
        return error(
            "TRANSFER_NEEDS_RECONCILE",
            "Goods already left the source warehouse. Receive remaining qty or resolve discrepancies.",
            409,
        )
    transfer.write({"state": "rejected", "note": _merge_note(transfer, payload, "rejected")})
    return success(serialize_transfer(transfer, detail=True))


def close_transfer(partner, transfer_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    transfer, err = _accessible_transfer(partner, transfer_id, assignment)
    if err:
        return err
    # Source or dest managers with create/receive can close completed transfers.
    allowed = set(assignment.warehouse_ids.ids)
    if transfer.source_warehouse_id.id not in allowed and transfer.dest_warehouse_id.id not in allowed:
        return error("WAREHOUSE_NOT_ALLOWED", "Warehouse not assigned.", 403)
    if not (assignment.perm_transfer_create or assignment.perm_transfer_receive):
        return error("WAREHOUSE_PERMISSION_DENIED", "Missing transfer permission.", 403)
    open_rows = transfer.discrepancy_ids.filtered(lambda row: row.state == "open")
    if open_rows:
        return error(
            "TRANSFER_OPEN_DISCREPANCIES",
            "Resolve open discrepancies before closing.",
            409,
            extra={"open_discrepancy_ids": open_rows.ids},
        )
    if transfer.state not in ("received", "discrepancy", "partially_received"):
        return error(
            "TRANSFER_INVALID_STATE",
            "Only received/partially received transfers (with no open discrepancies) can be closed.",
            409,
        )
    transfer.write({"state": "closed", "note": _merge_note(transfer, payload, "closed")})
    return success(serialize_transfer(transfer, detail=True))


def resolve_discrepancy(partner, discrepancy_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    if not (assignment.perm_transfer_create or assignment.perm_transfer_receive):
        return error("WAREHOUSE_PERMISSION_DENIED", "Missing transfer permission.", 403)
    row = partner.env["almohit.warehouse.discrepancy"].sudo().browse(discrepancy_id).exists()
    if not row:
        return error("DISCREPANCY_NOT_FOUND", "Discrepancy not found.", 404)
    transfer = row.transfer_id
    allowed = set(assignment.warehouse_ids.ids)
    if (
        transfer.source_warehouse_id.id not in allowed
        and transfer.dest_warehouse_id.id not in allowed
    ):
        return error("DISCREPANCY_NOT_FOUND", "Discrepancy not found.", 404)
    if row.state != "open":
        return error("DISCREPANCY_ALREADY_RESOLVED", "Discrepancy already resolved.", 409)
    note = ""
    if isinstance(payload, dict):
        note = (payload.get("note") or "").strip()
    row.write({
        "state": "reconciled",
        "note": note or row.note,
        "resolved_by_id": partner.env.user.id,
        "resolved_at": fields.Datetime.now(),
    })
    open_left = transfer.discrepancy_ids.filtered(lambda item: item.state == "open")
    if not open_left and transfer.state == "discrepancy":
        # All lines fully received → received; else partially_received
        all_received = all(
            float(line.qty_received or 0.0) + 1e-6 >= float(line.qty_dispatched or 0.0)
            for line in transfer.line_ids
            if float(line.qty_dispatched or 0.0) > 0
        )
        transfer.state = "received" if all_received else "partially_received"
    return success({
        "discrepancy": {
            "id": row.id,
            "state": row.state,
            "resolved_by": named(row.resolved_by_id),
            "resolved_at": row.resolved_at or None,
            "note": row.note or None,
        },
        "transfer": serialize_transfer(transfer, detail=True),
    })


def _merge_note(transfer, payload, action):
    extra = ""
    if isinstance(payload, dict):
        extra = (payload.get("note") or "").strip()
    stamp = "%s by user %s" % (action, transfer.env.user.id)
    parts = [p for p in [(transfer.note or "").strip(), stamp, extra] if p]
    return " | ".join(parts)
