"""Warehouse manager API facade (phases 1–7)."""
from .customer import serialize_warehouse_access
from .api import success
from .warehouse_common import assignment_or_error, allowed_location_ids
from . import warehouse_receipts
from . import warehouse_issues
from . import warehouse_transfers
from . import warehouse_inventory


def list_my_warehouses(partner):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    access = serialize_warehouse_access(partner)
    return success({
        "warehouses": access["warehouses"],
        "default_warehouse_id": access["default_warehouse_id"],
        "permissions": access["permissions"],
    })


def dashboard(partner):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    warehouses = assignment.warehouse_ids
    location_ids = allowed_location_ids(warehouses)
    Quant = partner.env["stock.quant"].sudo()
    product_ids = Quant.search([
        ("location_id", "in", location_ids),
        ("quantity", ">", 0),
    ]).mapped("product_id").ids if location_ids else []
    access = serialize_warehouse_access(partner)
    return success({
        "warehouse_count": len(warehouses),
        "products_in_stock": len(set(product_ids)),
        "pending_receipts": warehouse_receipts.count_pending_receipts(assignment),
        "pending_issues": warehouse_issues.count_pending_issues(assignment),
        "incoming_transfers": warehouse_transfers.count_incoming_transfers(assignment),
        "outgoing_transfers": warehouse_transfers.count_outgoing_transfers(assignment),
        "stock_variances": warehouse_transfers.count_open_discrepancies(assignment),
        "warehouses": access["warehouses"],
        "default_warehouse_id": access["default_warehouse_id"],
        "permissions": access["permissions"],
    })


def list_stock(partner, kwargs):
    from .warehouse_common import require_perm
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_stock_read", "stock_read")
    if err:
        return err
    return _list_stock_impl(partner, kwargs, assignment)


def _list_stock_impl(partner, kwargs, assignment):
    from .api import error, parse_pagination, success as ok
    from .warehouse_common import strict_int, warehouse_for_location

    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err
    warehouses = assignment.warehouse_ids
    raw_warehouse_id = kwargs.get("warehouse_id")
    if raw_warehouse_id not in (None, ""):
        try:
            warehouse_id = int(raw_warehouse_id)
        except (TypeError, ValueError):
            return error("INVALID_FILTER", "warehouse_id must be a positive integer.", 400)
        if warehouse_id < 1 or warehouse_id not in warehouses.ids:
            return error("WAREHOUSE_NOT_ALLOWED", "Warehouse is not assigned to this account.", 403)
        warehouses = warehouses.browse(warehouse_id)

    product_id = None
    raw_product_id = kwargs.get("product_id")
    if raw_product_id not in (None, ""):
        try:
            product_id = int(raw_product_id)
        except (TypeError, ValueError):
            return error("INVALID_FILTER", "product_id must be a positive integer.", 400)
        if product_id < 1:
            return error("INVALID_FILTER", "product_id must be a positive integer.", 400)

    location_ids = allowed_location_ids(warehouses)
    domain = [("location_id", "in", location_ids), ("quantity", "!=", 0)]
    if product_id:
        domain.append(("product_id", "=", product_id))
    Quant = partner.env["stock.quant"].sudo()
    total = Quant.search_count(domain)
    offset = (page - 1) * page_size
    quants = Quant.search(domain, offset=offset, limit=page_size, order="product_id, location_id, id")
    pages = 0 if total == 0 else (total + page_size - 1) // page_size
    warehouse_by_location = {}
    for warehouse in warehouses:
        child_ids = partner.env["stock.location"].sudo().search(
            [("id", "child_of", warehouse.view_location_id.id)]
        ).ids
        for location_id in child_ids:
            warehouse_by_location[location_id] = warehouse
    items = []
    for quant in quants:
        warehouse = warehouse_by_location.get(quant.location_id.id)
        product = quant.product_id
        items.append({
            "id": quant.id,
            "product": {
                "id": product.id,
                "name": product.display_name,
                "default_code": product.default_code or None,
                "barcode": product.barcode or None,
                "uom": (
                    {"id": product.uom_id.id, "name": product.uom_id.name}
                    if product.uom_id else None
                ),
            },
            "warehouse": (
                {"id": warehouse.id, "name": warehouse.name} if warehouse else None
            ),
            "location": {
                "id": quant.location_id.id,
                "name": quant.location_id.complete_name or quant.location_id.name,
            },
            "quantity": float(quant.quantity or 0.0),
            "reserved_quantity": float(quant.reserved_quantity or 0.0),
            "available_quantity": float(
                (quant.quantity or 0.0) - (quant.reserved_quantity or 0.0)
            ),
        })
    return ok({
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        },
    })


# Receipts
list_receipts = warehouse_receipts.list_receipts
get_receipt = warehouse_receipts.get_receipt
receive_receipt = warehouse_receipts.receive_receipt

# Issues
list_issues = warehouse_issues.list_issues
get_issue = warehouse_issues.get_issue
dispatch_issue = warehouse_issues.dispatch_issue

# Transfers
list_transfers = warehouse_transfers.list_transfers
get_transfer = warehouse_transfers.get_transfer
create_transfer = warehouse_transfers.create_transfer
dispatch_transfer = warehouse_transfers.dispatch_transfer
receive_transfer = warehouse_transfers.receive_transfer
cancel_transfer = warehouse_transfers.cancel_transfer
reject_transfer = warehouse_transfers.reject_transfer
close_transfer = warehouse_transfers.close_transfer
resolve_discrepancy = warehouse_transfers.resolve_discrepancy

# Inventory
list_inventory_sessions = warehouse_inventory.list_sessions
get_inventory_session = warehouse_inventory.get_session
preview_inventory_import = warehouse_inventory.preview_import
approve_inventory_session = warehouse_inventory.approve_session


def list_movements(partner, kwargs):
    """Stock moves affecting assigned warehouses (read-only report)."""
    from .api import error, parse_pagination, parse_date_range_params, success as ok, _query_scalar
    from datetime import datetime, time

    assignment, err = assignment_or_error(partner)
    if err:
        return err
    if not assignment.perm_stock_read:
        from .api import error as err_fn
        return err_fn("WAREHOUSE_PERMISSION_DENIED", "Missing permission: stock_read.", 403)
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err
    location_ids = allowed_location_ids(assignment.warehouse_ids)
    domain = [
        ("company_id", "=", partner.env.company.id),
        ("state", "=", "done"),
        "|",
        ("location_id", "in", location_ids),
        ("location_dest_id", "in", location_ids),
    ]
    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("date", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("date", "<=", datetime.combine(date_to, time(23, 59, 59))))
    Move = partner.env["stock.move"].sudo()
    total = Move.search_count(domain)
    offset = (page - 1) * page_size
    moves = Move.search(domain, offset=offset, limit=page_size, order="date desc, id desc")
    pages = 0 if total == 0 else (total + page_size - 1) // page_size
    items = []
    for move in moves:
        items.append({
            "id": move.id,
            "date": move.date or None,
            "reference": move.reference or move.picking_id.name or None,
            "product": {
                "id": move.product_id.id,
                "name": move.product_id.display_name,
                "default_code": move.product_id.default_code or None,
            },
            "quantity": float(move.quantity if "quantity" in move._fields else move.product_uom_qty or 0.0),
            "uom": {"id": move.product_uom.id, "name": move.product_uom.name} if move.product_uom else None,
            "location_from": {"id": move.location_id.id, "name": move.location_id.complete_name},
            "location_to": {"id": move.location_dest_id.id, "name": move.location_dest_id.complete_name},
            "picking_id": move.picking_id.id if move.picking_id else None,
        })
    return ok({
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        },
    })
