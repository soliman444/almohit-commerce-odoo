"""Sales stock issues — warehouse dispatch (stock→output) for storefront deliveries.

Dispatch contract: every line must send move_id + quantity_done + warehouse_id.
Optional request-level distributor_user_id assigns native stock.picking.user_id.
Different warehouses in one request each get a native stock→output internal
picking for that warehouse, then customer delivery moves are relocated to the
matching Output (delivery stays open for the distributor).
"""
from collections import defaultdict
from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_date_range_params, parse_pagination, success, _query_scalar
from .payments import order_allows_distribution
from .stock import is_inventory_managed
from .warehouse_common import (
    assignment_or_error,
    allowed_location_ids,
    dispatchable_qty,
    ensure_confirmed_assigned,
    filter_assignment_warehouses,
    finish_validate,
    location_in_warehouses,
    move_posted_qty,
    move_prepared_qty,
    move_reserved_qty,
    named,
    native_error,
    require_perm,
    set_move_done_qty,
    strict_float,
    strict_int,
    sudo_picking,
    warehouse_for_location,
)


class _IssueAbort(Exception):
    def __init__(self, response):
        super().__init__("issue abort")
        self.response = response


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


def _serialize_issue_line(move, company_warehouses=None):
    ordered = float(move.product_uom_qty or 0.0)
    reserved = move_reserved_qty(move)
    done = move_posted_qty(move)
    prepared = move_prepared_qty(move)
    company_warehouses = company_warehouses or move.env["stock.warehouse"]
    warehouse = warehouse_for_location(move.location_id, company_warehouses)
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
        "done_qty": done,
        "remaining_qty": max(ordered - done, 0.0),
        "uom": named(move.product_uom),
    }


def _company_warehouses(picking):
    return picking.env["stock.warehouse"].sudo().search([
        ("company_id", "=", picking.company_id.id),
        ("active", "=", True),
    ])


def _suggested_warehouse(picking, sale, company_warehouses):
    """Informational only — dispatch uses per-line warehouse_id."""
    if sale and sale.warehouse_id:
        return sale.warehouse_id
    type_wh = picking.picking_type_id.warehouse_id if picking.picking_type_id else False
    if type_wh:
        return type_wh
    return warehouse_for_location(picking.location_id, company_warehouses)


def _current_warehouse(picking, company_warehouses):
    current = warehouse_for_location(picking.location_id, company_warehouses)
    if current:
        return current
    return picking.picking_type_id.warehouse_id if picking.picking_type_id else current


def _active_issue_moves(picking):
    return picking.move_ids.filtered(
        lambda move: not move.scrapped and move.state != "cancel"
    )


def _available_source_warehouses(assignment, picking):
    """Assigned company warehouses with free-qty hints per open issue line."""
    moves = _active_issue_moves(picking)
    warehouses = filter_assignment_warehouses(assignment, picking.company_id)
    rows = []
    for warehouse in warehouses.sorted(key=lambda row: (row.name or "", row.id)):
        line_rows = []
        for move in moves:
            product = move.product_id
            ordered = float(move.product_uom_qty or 0.0)
            if not is_inventory_managed(product):
                free = None
            else:
                # Same lot_stock scope as dispatch execution (excludes Output).
                free = dispatchable_qty(product, warehouse)
            line_rows.append({
                "product_id": product.id,
                "product_name": product.display_name,
                "ordered_qty": ordered,
                "free_qty": free,
            })
        rows.append({
            "id": warehouse.id,
            "name": warehouse.display_name or warehouse.name or "",
            "lines": line_rows,
        })
    return rows


def serialize_issue(picking, detail=False, assignment=None, result_pickings=None):
    sale = _sale_of_picking(picking)
    company_warehouses = _company_warehouses(picking)
    current = _current_warehouse(picking, company_warehouses)
    suggested = _suggested_warehouse(picking, sale, company_warehouses)
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
        "warehouse": named(current),
        "distributor_user_id": picking.user_id.id if picking.user_id else None,
        "payment_ok": bool(sale and order_allows_distribution(sale)) if sale else True,
    }
    if detail:
        moves = picking.move_ids.filtered(lambda move: not move.scrapped)
        item["lines"] = [
            _serialize_issue_line(move, company_warehouses) for move in moves
        ]
        item["dispatch_picking_id"] = (
            picking.almohit_dispatch_picking_id.id
            if picking.almohit_dispatch_picking_id
            else None
        )
        linked_dispatches = picking.env["stock.picking"]
        if "almohit_dispatch_picking_ids" in picking._fields:
            linked_dispatches = picking.almohit_dispatch_picking_ids
        elif picking.almohit_dispatch_picking_id:
            linked_dispatches = picking.almohit_dispatch_picking_id
        item["dispatch_picking_ids"] = list(linked_dispatches.ids)
        # Informational only — dispatch requires warehouse_id on each POST line.
        item["suggested_warehouse"] = named(suggested)
        item["current_warehouse"] = named(current)
        item["available_warehouses"] = (
            _available_source_warehouses(assignment, picking) if assignment else []
        )
        item["dispatch_hint"] = (
            "Choose warehouse_id per line on POST .../dispatch from "
            "available_warehouses or GET /warehouse/my-warehouses. "
            "current/suggested are not required. Dispatch is stock→output only; "
            "customer delivery stays open for the distributor."
        )
        rows_for_result = result_pickings if result_pickings is not None else linked_dispatches
        if rows_for_result:
            item["result_pickings"] = []
            for row in rows_for_result:
                row_wh = warehouse_for_location(row.location_id, company_warehouses)
                if not row_wh and row.picking_type_id:
                    row_wh = row.picking_type_id.warehouse_id
                item["result_pickings"].append({
                    "id": row.id,
                    "name": row.name,
                    "state": row.state,
                    "warehouse": named(row_wh) if row_wh else None,
                    "origin": row.origin or None,
                })
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
        allowed = allowed_location_ids(assignment.warehouse_ids)
        move_ok = any(move.location_id.id in allowed for move in picking.move_ids)
        if not move_ok:
            return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)
    return success(serialize_issue(picking, detail=True, assignment=assignment))


def _lock_issue(picking):
    picking.env.cr.execute(
        "SELECT pg_try_advisory_xact_lock(%s, %s)",
        (87110425, int(picking.id)),
    )
    if not picking.env.cr.fetchone()[0]:
        return error(
            "ISSUE_BUSY",
            "Issue is being updated by another request. Retry shortly.",
            409,
        )
    picking.env.cr.execute(
        "SELECT id FROM stock_picking WHERE id = %s FOR UPDATE",
        (picking.id,),
    )
    return None


def _distributor_user_domain(env, company=None):
    company = company or env.company
    return [
        ("active", "=", True),
        ("partner_id.almohit_app_role", "=", "distributor"),
        ("partner_id.almohit_trade_status", "=", "approved"),
        "|",
        ("company_ids", "in", [company.id]),
        ("company_id", "=", company.id),
    ]


def list_distributors(partner):
    """Active assignable distributors for warehouse managers (id + name only)."""
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_issue", "issue")
    if err:
        return err
    users = partner.env["res.users"].sudo().search(
        _distributor_user_domain(partner.env),
        order="name, id",
    )
    return success({
        "items": [
            {
                "id": user.id,
                "name": user.name or (user.partner_id.name if user.partner_id else "") or "",
            }
            for user in users
        ],
    })


def _resolve_distributor_user(env, raw_user_id, company=None):
    """Validate optional distributor_user_id → res.users (no DB field added)."""
    user_id = strict_int(raw_user_id)
    if not user_id:
        return None, error(
            "INVALID_FIELDS",
            "distributor_user_id must be a positive integer.",
            400,
        )
    company = company or env.company
    user = env["res.users"].sudo().search(
        _distributor_user_domain(env, company) + [("id", "=", user_id)],
        limit=1,
    )
    if not user:
        return None, error(
            "DISTRIBUTOR_NOT_FOUND",
            "Distributor user not found or not assignable.",
            404,
        )
    return user, None


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


def _assert_warehouse_dispatch_ready(warehouse):
    if not warehouse.lot_stock_id:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse is missing its stock location.",
            400,
        )
    if not warehouse.int_type_id:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse has no internal transfer operation type.",
            400,
        )
    output = _output_location(warehouse)
    if not output:
        return error(
            "WAREHOUSE_CONFIG",
            "Warehouse needs a distinct Output (or Pack) location for dispatch "
            "so distributor delivery is not duplicated on the same stock location.",
            400,
        )
    return None


def _create_warehouse_dispatch_picking(customer_picking, warehouse, move_qty_rows):
    """Create and validate one stock→output internal picking for a warehouse.

    move_qty_rows: list of (customer_move, qty)
    Returns the done dispatch picking.
    """
    output = _output_location(warehouse)
    stock_loc = warehouse.lot_stock_id
    internal_type = warehouse.int_type_id

    move_commands = []
    for move, qty in move_qty_rows:
        # Same helper as available_warehouses[].free_qty (lot_stock, strict=False).
        available = dispatchable_qty(move.product_id, warehouse)
        if qty > available + 1e-6:
            raise _IssueAbort(
                error(
                    "INSUFFICIENT_STOCK",
                    "Insufficient free stock for %s in warehouse %s."
                    % (move.product_id.display_name, warehouse.display_name),
                    409,
                    {
                        "product_id": move.product_id.id,
                        "requested_qty": qty,
                        "available_qty": available,
                        "warehouse_id": warehouse.id,
                    },
                )
            )
        move_commands.append((0, 0, {
            "name": move.product_id.display_name,
            "product_id": move.product_id.id,
            "product_uom_qty": qty,
            "product_uom": move.product_uom.id,
            "location_id": stock_loc.id,
            "location_dest_id": output.id,
            "company_id": customer_picking.company_id.id,
        }))

    dispatch_vals = {
        "picking_type_id": internal_type.id,
        "location_id": stock_loc.id,
        "location_dest_id": output.id,
        "origin": "WH-DISPATCH/%s" % customer_picking.name,
        "partner_id": customer_picking.partner_id.id,
        "company_id": customer_picking.company_id.id,
        "move_ids": move_commands,
    }
    dispatch = (
        customer_picking.env["stock.picking"]
        .sudo()
        .with_context(skip_almohit_payment_assign=True)
        .create(dispatch_vals)
    )
    dispatch = sudo_picking(dispatch)
    ensure_confirmed_assigned(dispatch)
    for move in dispatch.move_ids:
        set_move_done_qty(move, float(move.product_uom_qty or 0.0))
    result = dispatch.button_validate()
    finish_validate(dispatch, result)
    dispatch.invalidate_recordset()
    if dispatch.state != "done":
        raise _IssueAbort(
            error(
                "ISSUE_DISPATCH_INCOMPLETE",
                "Warehouse dispatch picking %s was not fully validated."
                % (dispatch.name or dispatch.id),
                409,
            )
        )
    return dispatch


def _relocate_customer_moves_to_output(customer_picking, move_warehouse_map):
    """Point dispatched customer moves at their warehouse Output for distributor pickup.

    Does NOT validate the customer delivery and does NOT touch user_id.
    move_warehouse_map: {move_id: warehouse}
    """
    customer = sudo_picking(customer_picking)
    outputs = set()
    for move in customer.move_ids.filtered(lambda row: row.state not in ("done", "cancel")):
        warehouse = move_warehouse_map.get(move.id)
        if not warehouse:
            continue
        output = _output_location(warehouse)
        if not output:
            raise _IssueAbort(
                error(
                    "WAREHOUSE_CONFIG",
                    "Warehouse %s is missing Output for customer relocation."
                    % warehouse.display_name,
                    400,
                )
            )
        move.write({"location_id": output.id})
        for ml in move.move_line_ids:
            ml.write({"location_id": output.id})
        outputs.add(output.id)

    vals = {
        "almohit_dispatch_state": "dispatched",
    }
    if len(outputs) == 1:
        vals["location_id"] = next(iter(outputs))
    return vals


def dispatch_issue(partner, picking_id, payload):
    """Move goods stock→output per line warehouse without validating customer delivery."""
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_issue", "issue")
    if err:
        return err
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
    extra = set(payload) - {"lines", "distributor_user_id"}
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)

    distributor_user = None
    if "distributor_user_id" in payload:
        # null/0/empty are invalid — omitting the key leaves current assignee unchanged.
        distributor_user, dist_err = _resolve_distributor_user(
            partner.env, payload.get("distributor_user_id"), partner.env.company
        )
        if dist_err:
            return dist_err

    picking = partner.env["stock.picking"].sudo().browse(picking_id).exists()
    if not picking or picking.picking_type_code != "outgoing":
        return error("ISSUE_NOT_FOUND", "Stock issue not found.", 404)
    # State/idempotency before location scope so retries after stock→output
    # relocation return stable 409 codes instead of 404.
    if picking.state in ("done", "cancel"):
        return error("ISSUE_ALREADY_DONE", "Customer delivery is already closed.", 409)
    if picking.almohit_dispatch_state == "dispatched":
        return error("ISSUE_ALREADY_DISPATCHED", "Already dispatched from warehouse.", 409)

    assignment_locations = set(allowed_location_ids(assignment.warehouse_ids))
    if picking.location_id.id not in assignment_locations and not any(
        move.location_id.id in assignment_locations for move in picking.move_ids
    ):
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

    lock_err = _lock_issue(picking)
    if lock_err:
        return lock_err
    picking.invalidate_recordset()
    if picking.state in ("done", "cancel"):
        return error("ISSUE_ALREADY_DONE", "Customer delivery is already closed.", 409)
    if picking.almohit_dispatch_state == "dispatched":
        return error("ISSUE_ALREADY_DISPATCHED", "Already dispatched from warehouse.", 409)

    company_warehouses = filter_assignment_warehouses(assignment, picking.company_id)
    customer_picking = sudo_picking(picking)
    try:
        ensure_confirmed_assigned(customer_picking)
    except (UserError, ValidationError) as exc:
        return native_error("ISSUE_NOT_READY", exc)

    open_moves = {
        move.id: move
        for move in customer_picking.move_ids
        if move.state not in ("done", "cancel") and not move.scrapped
    }

    seen = set()
    parsed = []  # (move, qty, warehouse)
    for line in lines:
        if not isinstance(line, dict):
            return error("INVALID_FIELDS", "Each issue line must be an object.", 400)
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
            return error("ISSUE_LINE_NOT_FOUND", "Issue line not found.", 404)
        if move.state == "done":
            return error(
                "ISSUE_LINE_ALREADY_DONE",
                "Move %s was already delivered; warehouse cannot be changed." % move_id,
                409,
            )

        ordered = float(move.product_uom_qty or 0.0)
        if qty > ordered + 1e-6:
            return error(
                "INVALID_QUANTITY",
                "quantity_done cannot exceed ordered quantity for move %s." % move_id,
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
        cfg_err = _assert_warehouse_dispatch_ready(warehouse)
        if cfg_err:
            return cfg_err
        # Stock availability is checked after do_unreserve inside the savepoint
        # (reservations on this delivery would otherwise undercount free_qty).
        parsed.append((move, qty, warehouse))
    by_warehouse = defaultdict(list)
    for move, qty, warehouse in parsed:
        by_warehouse[warehouse.id].append((move, qty, warehouse))

    original_id = picking.id
    try:
        with partner.env.cr.savepoint():
            # Release this delivery's reservations so stock→output can take the
            # same goods (free_qty otherwise excludes qty reserved here).
            if hasattr(customer_picking, "do_unreserve"):
                customer_picking.do_unreserve()

            result_pickings = picking.env["stock.picking"]
            move_warehouse_map = {}
            primary_dispatch = picking.env["stock.picking"]

            for _wh_id, rows in by_warehouse.items():
                warehouse = rows[0][2]
                move_qty_rows = [(row[0], row[1]) for row in rows]
                try:
                    dispatch = _create_warehouse_dispatch_picking(
                        customer_picking, warehouse, move_qty_rows
                    )
                except (UserError, ValidationError) as exc:
                    raise _IssueAbort(native_error("ISSUE_DISPATCH_FAILED", exc))
                result_pickings |= dispatch
                if not primary_dispatch:
                    primary_dispatch = dispatch
                for move, _qty, _wh in rows:
                    move_warehouse_map[move.id] = warehouse

            relocate_vals = _relocate_customer_moves_to_output(
                customer_picking, move_warehouse_map
            )
            if primary_dispatch:
                relocate_vals["almohit_dispatch_picking_id"] = primary_dispatch.id
            if result_pickings and "almohit_dispatch_picking_ids" in customer_picking._fields:
                relocate_vals["almohit_dispatch_picking_ids"] = [(6, 0, result_pickings.ids)]
            # Relocation must not skip payment gates that apply to user_id writes —
            # only stock/dispatch metadata uses skip_almohit_payment_assign.
            customer_picking.with_context(skip_almohit_payment_assign=True).write(
                relocate_vals
            )

            if distributor_user:
                # Write native stock.picking.user_id without skip so payment
                # assert + distribution WhatsApp hooks still run. Same savepoint
                # as stock moves → full rollback if assign fails.
                try:
                    customer_picking.write({"user_id": distributor_user.id})
                except (UserError, ValidationError) as exc:
                    raise _IssueAbort(native_error("DISTRIBUTOR_ASSIGN_FAILED", exc))

            # Reload for response.
            original = partner.env["stock.picking"].sudo().browse(original_id)
            data = serialize_issue(
                original,
                detail=True,
                assignment=assignment,
                result_pickings=result_pickings,
            )
            return success(data)
    except _IssueAbort as abort:
        return abort.response
    except (UserError, ValidationError) as exc:
        return native_error("ISSUE_DISPATCH_FAILED", exc)
