"""Warehouse packing / manufacturing over native Odoo MRP.

Phase 1: list BoMs and manufacturing orders scoped to assigned warehouses.
Phase 2: create/update warehouse-scoped BoMs; create draft manufacturing orders.
Phase 3: confirm + complete via native mrp.production (no direct stock.quant writes).
"""
import math
import re
from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero
from .api import (
    error,
    paginated_success,
    parse_date_range_params,
    parse_pagination,
    success,
    _query_scalar,
)
from .warehouse_common import (
    assignment_or_error,
    allowed_location_ids,
    filter_assignment_warehouses,
    location_in_warehouses,
    named,
    native_error,
    require_perm,
    strict_float,
    strict_int,
    warehouse_for_location,
)


class _PackingAbort(Exception):
    """Abort packing mutation and roll back the active savepoint."""

    def __init__(self, response):
        super().__init__("packing abort")
        self.response = response

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_IDEMPOTENCY_ORIGIN_PREFIX = "almohit:packing:"


def _product_payload(product):
    if not product:
        return None
    return {
        "id": product.id,
        "name": product.display_name,
        "default_code": product.default_code or None,
        "barcode": product.barcode or None,
        "uom": named(product.uom_id) if product.uom_id else None,
    }


def _bom_finished_product(bom):
    if bom.product_id:
        return bom.product_id
    variants = bom.product_tmpl_id.product_variant_ids.filtered("active")
    return variants[:1]


def _warehouse_of_picking_type(picking_type):
    if not picking_type:
        return picking_type.env["stock.warehouse"]
    return picking_type.warehouse_id


def _mrp_picking_type_for_warehouse(warehouse):
    if not warehouse:
        return warehouse.env["stock.picking.type"]
    PickingType = warehouse.env["stock.picking.type"].sudo()
    return PickingType.search(
        [
            ("code", "=", "mrp_operation"),
            ("warehouse_id", "=", warehouse.id),
            ("company_id", "in", [False, warehouse.company_id.id]),
        ],
        limit=1,
    )


def _bom_allowed_for_assignment(bom, assignment):
    """BoM is readable when company matches and operation type (if set) is assigned."""
    company = assignment.env.company
    if bom.company_id and bom.company_id != company:
        return False
    warehouses = filter_assignment_warehouses(assignment, company)
    if not warehouses:
        return False
    picking_type = bom.picking_type_id
    if not picking_type:
        # Unscoped BoM can be run later against any assigned warehouse MO type.
        return True
    warehouse = _warehouse_of_picking_type(picking_type)
    return bool(warehouse and warehouse.id in warehouses.ids)


def _bom_writable_for_assignment(bom, assignment):
    """Editable only when BoM is warehouse-scoped to an assigned warehouse.

    Shared / unscoped BoMs (no picking_type_id) are read-only via this API.
    """
    if not _bom_allowed_for_assignment(bom, assignment):
        return False
    picking_type = bom.picking_type_id
    if not picking_type:
        return False
    warehouse = _warehouse_of_picking_type(picking_type)
    warehouses = filter_assignment_warehouses(assignment)
    return bool(warehouse and warehouse.id in warehouses.ids)


def _bom_usable_for_warehouse(bom, warehouse, assignment):
    if not _bom_allowed_for_assignment(bom, assignment):
        return False
    if bom.type != "normal":
        return False
    if bom.company_id and bom.company_id != assignment.env.company:
        return False
    picking_type = bom.picking_type_id
    if not picking_type:
        return True
    bom_wh = _warehouse_of_picking_type(picking_type)
    return bool(bom_wh and bom_wh.id == warehouse.id)


def serialize_bom(bom, detail=False):
    finished = _bom_finished_product(bom)
    picking_type = bom.picking_type_id
    warehouse = _warehouse_of_picking_type(picking_type) if picking_type else False
    item = {
        "id": bom.id,
        "code": bom.code or None,
        "type": bom.type,
        "product": _product_payload(finished),
        "product_tmpl_id": bom.product_tmpl_id.id if bom.product_tmpl_id else None,
        "product_qty": float(bom.product_qty or 0.0),
        "uom": named(bom.product_uom_id) if bom.product_uom_id else None,
        "picking_type": named(picking_type) if picking_type else None,
        "warehouse": named(warehouse) if warehouse else None,
        "company_id": bom.company_id.id if bom.company_id else None,
        "shared": not bool(picking_type),
    }
    if detail:
        item["components"] = [
            {
                "id": line.id,
                "product": _product_payload(line.product_id),
                "product_qty": float(line.product_qty or 0.0),
                "uom": named(line.product_uom_id) if line.product_uom_id else None,
            }
            for line in bom.bom_line_ids
        ]
        item["byproducts"] = [
            {
                "id": line.id,
                "product": _product_payload(line.product_id),
                "product_qty": float(line.product_qty or 0.0),
                "uom": named(line.product_uom_id) if line.product_uom_id else None,
                "cost_share": float(line.cost_share or 0.0),
            }
            for line in bom.byproduct_ids
        ]
    return item


def _move_done_qty(move):
    if "quantity" in move._fields:
        return float(move.quantity or 0.0)
    if "quantity_done" in move._fields:
        return float(move.quantity_done or 0.0)
    return 0.0


def _serialize_stock_move(move):
    planned = float(move.product_uom_qty or 0.0)
    done = _move_done_qty(move)
    reserved = 0.0
    if move.state not in ("done", "cancel"):
        if "reserved_availability" in move._fields:
            reserved = float(move.reserved_availability or 0.0)
    posted = done if move.state == "done" else 0.0
    is_raw = bool(getattr(move, "raw_material_production_id", False))
    is_finished = bool(getattr(move, "production_id", False)) and not is_raw
    return {
        "move_id": move.id,
        "product": _product_payload(move.product_id),
        "planned_qty": planned,
        "product_uom_qty": planned,
        "reserved_qty": reserved,
        "consumed_qty": posted if is_raw else 0.0,
        "produced_qty": posted if is_finished else 0.0,
        "quantity_done": done,
        "uom": named(move.product_uom) if move.product_uom else None,
        "location": named(move.location_id),
        "location_dest": named(move.location_dest_id),
        "state": move.state,
        "is_byproduct": bool(getattr(move, "byproduct_id", False)),
        "picked": bool(getattr(move, "picked", False)),
    }


def serialize_production(production, detail=False, warehouses=None):
    warehouses = warehouses or production.env["stock.warehouse"].browse()
    warehouse = (
        production.warehouse_id
        if production.warehouse_id
        else warehouse_for_location(production.location_src_id, warehouses)
    )
    planned_qty = float(production.product_qty or 0.0)
    produced_qty = float(production.qty_produced or 0.0)
    producing_qty = float(production.qty_producing or 0.0)
    if (
        production.state == "done"
        and float_is_zero(produced_qty, precision_rounding=production.product_uom_id.rounding or 0.01)
    ):
        done_finished = production.move_finished_ids.filtered(
            lambda move: move.state == "done"
            and move.product_id == production.product_id
            and not getattr(move, "byproduct_id", False)
        )
        produced_qty = sum(_move_done_qty(move) for move in done_finished)
    item = {
        "id": production.id,
        "name": production.name,
        "state": production.state,
        "reservation_state": production.reservation_state or None,
        "bom": (
            {
                "id": production.bom_id.id,
                "code": production.bom_id.code or None,
                "display_name": production.bom_id.display_name,
            }
            if production.bom_id
            else None
        ),
        "product": _product_payload(production.product_id),
        "product_qty": planned_qty,
        "planned_qty": planned_qty,
        "qty_producing": producing_qty,
        "qty_produced": produced_qty,
        "produced_qty": produced_qty,
        "uom": named(production.product_uom_id) if production.product_uom_id else None,
        "source_location": named(production.location_src_id),
        "dest_location": named(production.location_dest_id),
        "warehouse": named(warehouse) if warehouse else None,
        "date_start": production.date_start or None,
        "date_finished": production.date_finished or None,
        "origin": production.origin or None,
        "lot_producing": named(production.lot_producing_id)
        if production.lot_producing_id
        else None,
    }
    if detail:
        raw_moves = production.move_raw_ids.filtered(
            lambda move: move.state != "cancel"
        )
        finished_moves = production.move_finished_ids.filtered(
            lambda move: move.state != "cancel"
        )
        main_finished = finished_moves.filtered(
            lambda move: move.product_id == production.product_id
            and not getattr(move, "byproduct_id", False)
        )
        byproduct_moves = finished_moves.filtered(
            lambda move: bool(getattr(move, "byproduct_id", False))
            or move.product_id != production.product_id
        )
        item["components"] = [_serialize_stock_move(move) for move in raw_moves]
        item["finished_moves"] = [
            _serialize_stock_move(move) for move in finished_moves
        ]
        item["byproducts"] = [_serialize_stock_move(move) for move in byproduct_moves]
        item["main_finished"] = [
            _serialize_stock_move(move) for move in main_finished
        ]
        # Waste/scrap is recorded via stock.scrap (if any), not via MO moves.
        scraps = production.env["stock.scrap"].sudo().search(
            [("production_id", "=", production.id), ("state", "=", "done")]
        )
        item["scraps"] = [
            {
                "id": scrap.id,
                "product": _product_payload(scrap.product_id),
                "quantity": float(scrap.scrap_qty or 0.0),
                "uom": named(scrap.product_uom_id) if scrap.product_uom_id else None,
                "state": scrap.state,
            }
            for scrap in scraps
        ]
        item["quantity_semantics"] = {
            "planned_qty": "Ordered / BoM-planned finished quantity (product_qty).",
            "consumed_qty": "Raw move quantity posted after MO is done.",
            "produced_qty": "Finished / by-product quantity posted after MO is done.",
            "scrap_qty": "Quantity moved to scrap via stock.scrap (deducted once).",
        }
    return item


def _production_in_assignment(production, assignment):
    warehouses = filter_assignment_warehouses(assignment, production.company_id)
    if not warehouses:
        return False
    if production.company_id and production.company_id != assignment.env.company:
        return False
    if location_in_warehouses(production.location_src_id, warehouses):
        return True
    if location_in_warehouses(production.location_dest_id, warehouses):
        return True
    if production.warehouse_id and production.warehouse_id.id in warehouses.ids:
        return True
    return False


def _production_scope_domain(assignment):
    warehouses = filter_assignment_warehouses(assignment)
    location_ids = allowed_location_ids(warehouses)
    if not location_ids:
        return [("id", "=", 0)]
    return [
        ("company_id", "=", assignment.env.company.id),
        "|",
        "|",
        ("location_src_id", "in", location_ids),
        ("location_dest_id", "in", location_ids),
        ("warehouse_id", "in", warehouses.ids),
    ]


def list_boms(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err

    Bom = partner.env["mrp.bom"].sudo()
    domain = [
        ("type", "=", "normal"),
        ("company_id", "in", [False, partner.env.company.id]),
    ]
    q = (_query_scalar(kwargs, "q") or "").strip()
    if q:
        domain += [
            "|",
            "|",
            ("code", "ilike", q),
            ("product_tmpl_id.name", "ilike", q),
            ("product_id.default_code", "ilike", q),
        ]
    product_id = strict_int(_query_scalar(kwargs, "product_id"))
    if product_id:
        domain += [
            "|",
            ("product_id", "=", product_id),
            "&",
            ("product_id", "=", False),
            ("product_tmpl_id.product_variant_ids", "in", [product_id]),
        ]

    boms = Bom.search(domain, order="sequence, id")
    boms = boms.filtered(lambda bom: _bom_allowed_for_assignment(bom, assignment))
    return paginated_success(
        boms,
        lambda bom: serialize_bom(bom, detail=True),
        page,
        page_size,
    )


def get_bom(partner, bom_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    bom = partner.env["mrp.bom"].sudo().browse(bom_id).exists()
    if not bom or bom.type != "normal" or not _bom_allowed_for_assignment(bom, assignment):
        return error("BOM_NOT_FOUND", "Bill of materials not found.", 404)
    return success(serialize_bom(bom, detail=True))


def list_orders(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err

    domain = _production_scope_domain(assignment)
    status = (_query_scalar(kwargs, "status") or "").strip().lower()
    if status:
        if status == "open":
            domain.append(("state", "not in", ("done", "cancel")))
        elif status in (
            "draft",
            "confirmed",
            "progress",
            "to_close",
            "done",
            "cancel",
        ):
            domain.append(("state", "=", status))
        else:
            return error(
                "INVALID_FILTER",
                "status must be open, draft, confirmed, progress, to_close, done, or cancel.",
                400,
            )
    else:
        domain.append(("state", "not in", ("done", "cancel")))

    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return err
    if date_from:
        domain.append(("date_start", ">=", datetime.combine(date_from, time.min)))
    if date_to:
        domain.append(("date_start", "<=", datetime.combine(date_to, time(23, 59, 59))))

    q = (_query_scalar(kwargs, "q") or "").strip()
    if q:
        domain += [
            "|",
            "|",
            ("name", "ilike", q),
            ("origin", "ilike", q),
            ("product_id.name", "ilike", q),
        ]

    warehouses = filter_assignment_warehouses(assignment)
    productions = partner.env["mrp.production"].sudo().search(domain, order="id desc")
    return paginated_success(
        productions,
        lambda production: serialize_production(
            production, detail=False, warehouses=warehouses
        ),
        page,
        page_size,
    )


def get_order(partner, production_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    production = partner.env["mrp.production"].sudo().browse(production_id).exists()
    if not production or not _production_in_assignment(production, assignment):
        return error("PACKING_ORDER_NOT_FOUND", "Manufacturing order not found.", 404)
    warehouses = filter_assignment_warehouses(assignment)
    return success(
        serialize_production(production, detail=True, warehouses=warehouses)
    )


def _positive_finite_qty(value, field_name="quantity"):
    qty = strict_float(value)
    if qty is None or not math.isfinite(qty) or qty <= 0:
        return None, error(
            "INVALID_FIELDS",
            "%s must be a positive finite number." % field_name,
            400,
        )
    return qty, None


def _load_active_product(env, product_id, label="Product"):
    product = env["product.product"].sudo().browse(product_id).exists()
    if not product or not product.active:
        return None, error(
            "PRODUCT_NOT_FOUND",
            "%s not found or inactive." % label,
            404,
        )
    if product.type not in ("product", "consu"):
        return None, error(
            "INVALID_PRODUCT",
            "%s must be a storable or consumable product." % label,
            400,
        )
    return product, None


def _parse_component_rows(env, rows, finished_product, field_name="components"):
    if not isinstance(rows, list) or not rows:
        return None, error(
            "INVALID_FIELDS",
            "%s must be a non-empty array." % field_name,
            400,
        )
    seen = set()
    commands = []
    for row in rows:
        if not isinstance(row, dict):
            return None, error(
                "INVALID_FIELDS",
                "Each %s entry must be an object." % field_name,
                400,
            )
        extra = set(row) - {"product_id", "quantity", "uom_id"}
        if extra:
            return None, error(
                "INVALID_FIELDS",
                "One or more %s fields are not allowed." % field_name,
                400,
            )
        product_id = strict_int(row.get("product_id"))
        qty, qty_err = _positive_finite_qty(row.get("quantity"), "quantity")
        if not product_id:
            return None, error(
                "INVALID_FIELDS",
                "product_id is required on each %s entry." % field_name,
                400,
            )
        if qty_err:
            return None, qty_err
        if product_id in seen:
            return None, error(
                "DUPLICATE_COMPONENT",
                "Duplicate component product_id=%s is not allowed." % product_id,
                400,
            )
        seen.add(product_id)
        product, prod_err = _load_active_product(env, product_id, "Component")
        if prod_err:
            return None, prod_err
        if finished_product and product.id == finished_product.id:
            return None, error(
                "INVALID_BOM",
                "Finished product cannot be used as a component of itself.",
                400,
            )
        if (
            finished_product
            and product.product_tmpl_id.id == finished_product.product_tmpl_id.id
            and not finished_product.product_tmpl_id.attribute_line_ids
        ):
            return None, error(
                "INVALID_BOM",
                "Finished product cannot be used as a component of itself.",
                400,
            )
        uom_id = strict_int(row.get("uom_id"))
        uom = env["uom.uom"].sudo().browse(uom_id).exists() if uom_id else product.uom_id
        if not uom:
            return None, error("UOM_NOT_FOUND", "Unit of measure not found.", 404)
        if uom.category_id != product.uom_id.category_id:
            return None, error(
                "INVALID_UOM",
                "Component UoM category does not match the product.",
                400,
            )
        commands.append(
            (
                0,
                0,
                {
                    "product_id": product.id,
                    "product_qty": qty,
                    "product_uom_id": uom.id,
                },
            )
        )
    return commands, None


def _parse_byproduct_rows(env, rows, finished_product):
    if rows in (None,):
        return [], None
    if not isinstance(rows, list):
        return None, error("INVALID_FIELDS", "byproducts must be an array.", 400)
    seen = set()
    commands = []
    for row in rows:
        if not isinstance(row, dict):
            return None, error(
                "INVALID_FIELDS",
                "Each byproducts entry must be an object.",
                400,
            )
        extra = set(row) - {"product_id", "quantity", "uom_id", "cost_share"}
        if extra:
            return None, error(
                "INVALID_FIELDS",
                "One or more byproducts fields are not allowed.",
                400,
            )
        product_id = strict_int(row.get("product_id"))
        qty, qty_err = _positive_finite_qty(row.get("quantity"), "quantity")
        if not product_id:
            return None, error(
                "INVALID_FIELDS",
                "product_id is required on each byproducts entry.",
                400,
            )
        if qty_err:
            return None, qty_err
        if product_id in seen:
            return None, error(
                "DUPLICATE_BYPRODUCT",
                "Duplicate byproduct product_id=%s is not allowed." % product_id,
                400,
            )
        seen.add(product_id)
        product, prod_err = _load_active_product(env, product_id, "By-product")
        if prod_err:
            return None, prod_err
        if finished_product and (
            product.id == finished_product.id
            or product.product_tmpl_id.id == finished_product.product_tmpl_id.id
        ):
            return None, error(
                "INVALID_BOM",
                "By-product cannot be the same as the finished product.",
                400,
            )
        uom_id = strict_int(row.get("uom_id"))
        uom = env["uom.uom"].sudo().browse(uom_id).exists() if uom_id else product.uom_id
        if not uom:
            return None, error("UOM_NOT_FOUND", "Unit of measure not found.", 404)
        if uom.category_id != product.uom_id.category_id:
            return None, error(
                "INVALID_UOM",
                "By-product UoM category does not match the product.",
                400,
            )
        cost_share = 0.0
        if "cost_share" in row and row.get("cost_share") is not None:
            cost_share = strict_float(row.get("cost_share"))
            if (
                cost_share is None
                or not math.isfinite(cost_share)
                or cost_share < 0
                or cost_share > 100
            ):
                return None, error(
                    "INVALID_FIELDS",
                    "cost_share must be between 0 and 100.",
                    400,
                )
        commands.append(
            (
                0,
                0,
                {
                    "product_id": product.id,
                    "product_qty": qty,
                    "product_uom_id": uom.id,
                    "cost_share": cost_share,
                },
            )
        )
    total_share = sum(cmd[2]["cost_share"] for cmd in commands)
    if total_share > 100 + 1e-6:
        return None, error(
            "INVALID_BOM",
            "Sum of byproduct cost_share must be <= 100.",
            400,
        )
    return commands, None


def _resolve_assigned_warehouse(partner, assignment, warehouse_id):
    warehouses = filter_assignment_warehouses(assignment)
    if warehouse_id not in warehouses.ids:
        return None, error(
            "WAREHOUSE_NOT_ALLOWED",
            "Warehouse is not assigned to this account.",
            403,
        )
    warehouse = partner.env["stock.warehouse"].sudo().browse(warehouse_id)
    picking_type = _mrp_picking_type_for_warehouse(warehouse)
    if not picking_type:
        return None, error(
            "WAREHOUSE_CONFIG",
            "Warehouse has no manufacturing operation type.",
            400,
        )
    return (warehouse, picking_type), None


def _bom_has_locked_productions(bom):
    Production = bom.env["mrp.production"].sudo()
    return bool(
        Production.search_count(
            [
                ("bom_id", "=", bom.id),
                ("state", "in", ("confirmed", "progress", "to_close", "done")),
            ]
        )
    )


def create_bom(partner, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing_bom_manage", "packing_bom_manage")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)

    allowed = {
        "product_id",
        "warehouse_id",
        "quantity",
        "code",
        "components",
        "byproducts",
    }
    extra = set(payload) - allowed
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    if "location_id" in payload or "location_dest_id" in payload:
        return error(
            "INVALID_FIELDS",
            "location_id and location_dest_id are not accepted.",
            400,
        )

    product_id = strict_int(payload.get("product_id"))
    warehouse_id = strict_int(payload.get("warehouse_id"))
    if not product_id or not warehouse_id:
        return error(
            "INVALID_FIELDS",
            "product_id and warehouse_id are required.",
            400,
        )
    qty, qty_err = _positive_finite_qty(payload.get("quantity"), "quantity")
    if qty_err:
        return qty_err

    product, prod_err = _load_active_product(partner.env, product_id, "Finished product")
    if prod_err:
        return prod_err
    resolved, wh_err = _resolve_assigned_warehouse(partner, assignment, warehouse_id)
    if wh_err:
        return wh_err
    _warehouse, picking_type = resolved

    component_cmds, comp_err = _parse_component_rows(
        partner.env, payload.get("components"), product
    )
    if comp_err:
        return comp_err
    byproduct_cmds, by_err = _parse_byproduct_rows(
        partner.env, payload.get("byproducts"), product
    )
    if by_err:
        return by_err

    code = (payload.get("code") or "").strip() or False
    vals = {
        "product_tmpl_id": product.product_tmpl_id.id,
        "product_id": product.id,
        "product_qty": qty,
        "product_uom_id": product.uom_id.id,
        "type": "normal",
        "company_id": partner.env.company.id,
        "picking_type_id": picking_type.id,
        "code": code,
        "bom_line_ids": component_cmds,
        "byproduct_ids": byproduct_cmds,
    }
    try:
        bom = partner.env["mrp.bom"].sudo().create(vals)
    except (UserError, ValidationError) as exc:
        return native_error("BOM_CREATE_FAILED", exc)
    return success(serialize_bom(bom, detail=True), status=201)


def update_bom(partner, bom_id, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing_bom_manage", "packing_bom_manage")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)

    bom = partner.env["mrp.bom"].sudo().browse(bom_id).exists()
    if not bom or bom.type != "normal":
        return error("BOM_NOT_FOUND", "Bill of materials not found.", 404)
    if not bom.picking_type_id:
        return error(
            "BOM_SHARED_READONLY",
            "Shared company BoMs without a warehouse operation type cannot be edited here.",
            403,
        )
    if not _bom_writable_for_assignment(bom, assignment):
        return error(
            "BOM_WAREHOUSE_DENIED",
            "BoM belongs to a warehouse that is not assigned to this account.",
            403,
        )
    if _bom_has_locked_productions(bom):
        return error(
            "BOM_IN_USE",
            "Cannot modify a BoM linked to confirmed or done manufacturing orders.",
            409,
        )

    allowed = {
        "product_id",
        "warehouse_id",
        "quantity",
        "code",
        "components",
        "byproducts",
    }
    extra = set(payload) - allowed
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    if "type" in payload:
        return error(
            "BOM_TYPE_LOCKED",
            "Changing BoM type (kit / subcontracting) is not allowed.",
            400,
        )
    if "location_id" in payload or "location_dest_id" in payload:
        return error(
            "INVALID_FIELDS",
            "location_id and location_dest_id are not accepted.",
            400,
        )
    if not payload:
        return error("INVALID_FIELDS", "No updatable fields provided.", 400)

    finished = _bom_finished_product(bom)
    vals = {}

    if "product_id" in payload:
        product_id = strict_int(payload.get("product_id"))
        if not product_id:
            return error("INVALID_FIELDS", "product_id must be a positive integer.", 400)
        product, prod_err = _load_active_product(
            partner.env, product_id, "Finished product"
        )
        if prod_err:
            return prod_err
        finished = product
        vals["product_tmpl_id"] = product.product_tmpl_id.id
        vals["product_id"] = product.id
        vals["product_uom_id"] = product.uom_id.id

    if "warehouse_id" in payload:
        warehouse_id = strict_int(payload.get("warehouse_id"))
        if not warehouse_id:
            return error(
                "INVALID_FIELDS",
                "warehouse_id must be a positive integer.",
                400,
            )
        resolved, wh_err = _resolve_assigned_warehouse(
            partner, assignment, warehouse_id
        )
        if wh_err:
            return wh_err
        _warehouse, picking_type = resolved
        vals["picking_type_id"] = picking_type.id

    if "quantity" in payload:
        qty, qty_err = _positive_finite_qty(payload.get("quantity"), "quantity")
        if qty_err:
            return qty_err
        vals["product_qty"] = qty

    if "code" in payload:
        vals["code"] = (payload.get("code") or "").strip() or False

    if "components" in payload:
        component_cmds, comp_err = _parse_component_rows(
            partner.env, payload.get("components"), finished
        )
        if comp_err:
            return comp_err
        vals["bom_line_ids"] = [(5, 0, 0)] + component_cmds

    if "byproducts" in payload:
        byproduct_cmds, by_err = _parse_byproduct_rows(
            partner.env, payload.get("byproducts"), finished
        )
        if by_err:
            return by_err
        vals["byproduct_ids"] = [(5, 0, 0)] + byproduct_cmds

    # Re-validate finished vs components if only one side changed.
    if "components" not in payload and "product_id" in payload:
        for line in bom.bom_line_ids:
            if line.product_id.id == finished.id or (
                line.product_id.product_tmpl_id.id == finished.product_tmpl_id.id
                and not finished.product_tmpl_id.attribute_line_ids
            ):
                return error(
                    "INVALID_BOM",
                    "Finished product cannot be used as a component of itself.",
                    400,
                )

    try:
        bom.write(vals)
    except (UserError, ValidationError) as exc:
        return native_error("BOM_UPDATE_FAILED", exc)
    return success(serialize_bom(bom, detail=True))


def _idempotency_origin(partner, key):
    return "%s%s:%s" % (_IDEMPOTENCY_ORIGIN_PREFIX, partner.id, key)


def create_order(partner, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)

    allowed = {"bom_id", "warehouse_id", "quantity", "idempotency_key"}
    extra = set(payload) - allowed
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    if "location_id" in payload or "location_dest_id" in payload:
        return error(
            "INVALID_FIELDS",
            "location_id and location_dest_id are not accepted.",
            400,
        )

    bom_id = strict_int(payload.get("bom_id"))
    warehouse_id = strict_int(payload.get("warehouse_id"))
    if not bom_id or not warehouse_id:
        return error(
            "INVALID_FIELDS",
            "bom_id and warehouse_id are required.",
            400,
        )
    qty, qty_err = _positive_finite_qty(payload.get("quantity"), "quantity")
    if qty_err:
        return qty_err

    raw_key = payload.get("idempotency_key")
    idem_key = None
    if raw_key is not None and raw_key != "":
        idem_key = str(raw_key).strip()
        if not _IDEMPOTENCY_RE.match(idem_key):
            return error(
                "INVALID_FIELDS",
                "idempotency_key must be 1..64 chars: letters, digits, . _ : -",
                400,
            )

    bom = partner.env["mrp.bom"].sudo().browse(bom_id).exists()
    if not bom or bom.type != "normal":
        return error("BOM_NOT_FOUND", "Bill of materials not found.", 404)

    resolved, wh_err = _resolve_assigned_warehouse(partner, assignment, warehouse_id)
    if wh_err:
        return wh_err
    warehouse, picking_type = resolved

    if not _bom_usable_for_warehouse(bom, warehouse, assignment):
        return error(
            "BOM_WAREHOUSE_MISMATCH",
            "BoM is not usable for the selected warehouse.",
            403,
        )

    finished = _bom_finished_product(bom)
    if not finished:
        return error(
            "INVALID_BOM",
            "BoM has no resolvable finished product variant.",
            400,
        )

    warehouses = filter_assignment_warehouses(assignment)
    if idem_key:
        origin = _idempotency_origin(partner, idem_key)
        existing = (
            partner.env["mrp.production"]
            .sudo()
            .search(
                [
                    ("origin", "=", origin),
                    ("company_id", "=", partner.env.company.id),
                ],
                order="id desc",
                limit=1,
            )
        )
        if existing:
            return success(
                serialize_production(
                    existing, detail=True, warehouses=warehouses
                ),
                status=200,
            )
    else:
        origin = False

    vals = {
        "product_id": finished.id,
        "product_qty": qty,
        "product_uom_id": finished.uom_id.id,
        "bom_id": bom.id,
        "picking_type_id": picking_type.id,
        "company_id": partner.env.company.id,
        "origin": origin,
    }
    # Let Odoo compute component/finished locations from the warehouse MO type.
    try:
        production = partner.env["mrp.production"].sudo().create(vals)
    except (UserError, ValidationError) as exc:
        return native_error("PACKING_ORDER_CREATE_FAILED", exc)

    if production.state != "draft":
        # Safety: never leave a non-draft MO from this endpoint.
        return error(
            "PACKING_ORDER_STATE",
            "Manufacturing order was not left in draft; aborted.",
            500,
        )

    return success(
        serialize_production(production, detail=True, warehouses=warehouses),
        status=201,
    )


def _lock_production(production):
    """Serialize concurrent confirm/complete without aborting the transaction.

    Uses a transaction-scoped advisory lock, then a row lock for state reads.
    """
    # 87110423 = stable namespace key for almohit packing MO locks
    production.env.cr.execute(
        "SELECT pg_try_advisory_xact_lock(%s, %s)",
        (87110423, int(production.id)),
    )
    if not production.env.cr.fetchone()[0]:
        return error(
            "PACKING_ORDER_BUSY",
            "Manufacturing order is being updated by another request. Retry shortly.",
            409,
        )
    production.env.cr.execute(
        "SELECT id FROM mrp_production WHERE id = %s FOR UPDATE",
        (production.id,),
    )
    return None


def _get_accessible_production(partner, production_id, assignment):
    production = partner.env["mrp.production"].sudo().browse(production_id).exists()
    if not production or not _production_in_assignment(production, assignment):
        return None, error(
            "PACKING_ORDER_NOT_FOUND",
            "Manufacturing order not found.",
            404,
        )
    if production.company_id and production.company_id != partner.env.company:
        return None, error(
            "CROSS_COMPANY_DENIED",
            "Resource belongs to another company.",
            403,
        )
    return production, None


def _warehouse_of_production(production, assignment):
    warehouses = filter_assignment_warehouses(assignment)
    if production.warehouse_id and production.warehouse_id.id in warehouses.ids:
        return production.warehouse_id
    warehouse = warehouse_for_location(production.location_src_id, warehouses)
    if warehouse:
        return warehouse
    return warehouse_for_location(production.location_dest_id, warehouses)


def confirm_order(partner, production_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    payload = payload if isinstance(payload, dict) else {}
    if payload:
        return error(
            "INVALID_FIELDS",
            "Confirm does not accept body fields (no location_id / location_dest_id).",
            400,
        )

    production, err = _get_accessible_production(partner, production_id, assignment)
    if err:
        return err

    warehouses = filter_assignment_warehouses(assignment)
    lock_err = _lock_production(production)
    if lock_err:
        return lock_err
    production.invalidate_recordset()

    if production.state in ("confirmed", "progress", "to_close"):
        # Idempotent retry: already confirmed, no duplicate move confirm.
        return success(
            serialize_production(production, detail=True, warehouses=warehouses)
        )
    if production.state == "done":
        return error(
            "PACKING_ORDER_ALREADY_DONE",
            "Manufacturing order is already done.",
            409,
        )
    if production.state == "cancel":
        return error(
            "PACKING_ORDER_CANCELLED",
            "Manufacturing order is cancelled.",
            409,
        )
    if production.state != "draft":
        return error(
            "PACKING_ORDER_INVALID_STATE",
            "Only draft manufacturing orders can be confirmed.",
            409,
        )

    warehouse = _warehouse_of_production(production, assignment)
    if not warehouse:
        return error(
            "WAREHOUSE_NOT_ALLOWED",
            "Manufacturing order warehouse is not assigned.",
            403,
        )
    if production.bom_id and not _bom_usable_for_warehouse(
        production.bom_id, warehouse, assignment
    ):
        return error(
            "BOM_WAREHOUSE_MISMATCH",
            "BoM is not usable for the manufacturing order warehouse.",
            403,
        )

    try:
        with partner.env.cr.savepoint():
            production.action_confirm()
            if hasattr(production, "action_assign"):
                production.action_assign()
    except (UserError, ValidationError) as exc:
        return native_error("PACKING_ORDER_CONFIRM_FAILED", exc)

    production.invalidate_recordset()
    return success(
        serialize_production(production, detail=True, warehouses=warehouses)
    )


def _non_negative_finite_qty(value, field_name="quantity"):
    qty = strict_float(value)
    if qty is None or not math.isfinite(qty) or qty < 0:
        return None, error(
            "INVALID_FIELDS",
            "%s must be a non-negative finite number." % field_name,
            400,
        )
    return qty, None


def _resolve_lot(env, product, lot_id=None, lot_name=None, company=None):
    if product.tracking in (False, "none"):
        return env["stock.lot"], None
    Lot = env["stock.lot"].sudo()
    if lot_id:
        lot = Lot.browse(strict_int(lot_id) or 0).exists()
        if not lot or lot.product_id != product:
            return None, error("LOT_NOT_FOUND", "Lot/serial not found for product.", 404)
        return lot, None
    name = (lot_name or "").strip()
    if not name:
        return None, error(
            "LOT_REQUIRED",
            "Lot/serial is required for tracked product %s." % product.display_name,
            400,
        )
    domain = [("product_id", "=", product.id), ("name", "=", name)]
    if company:
        domain.append(("company_id", "in", [False, company.id]))
    lot = Lot.search(domain, limit=1)
    if not lot:
        lot = Lot.create(
            {
                "name": name,
                "product_id": product.id,
                "company_id": company.id if company else False,
            }
        )
    return lot, None


def _set_move_quantity(move, qty, lot=None):
    """Set done quantity on an MO move using Odoo 17 fields."""
    rounding = move.product_uom.rounding
    if float_is_zero(qty, precision_rounding=rounding):
        if hasattr(move, "_set_quantity_done"):
            move._set_quantity_done(0.0)
        elif "quantity" in move._fields:
            move.quantity = 0.0
        if "picked" in move._fields:
            move.picked = False
        return

    if move.has_tracking in ("lot", "serial") and not lot:
        raise UserError(
            "Lot/serial required for product %s." % move.product_id.display_name
        )

    if hasattr(move, "_set_quantity_done"):
        move._set_quantity_done(qty)
    elif "quantity" in move._fields:
        move.quantity = qty
    elif "quantity_done" in move._fields:
        move.quantity_done = qty

    if "picked" in move._fields:
        move.picked = True

    # Attach lot on existing move lines when tracking is enabled.
    if lot and move.move_line_ids:
        line = move.move_line_ids[:1]
        vals = {"lot_id": lot.id}
        if "quantity" in line._fields:
            vals["quantity"] = qty
        if "qty_done" in line._fields:
            vals["qty_done"] = qty
        if "picked" in line._fields:
            vals["picked"] = True
        line.write(vals)


def _index_moves_by_product(moves):
    indexed = {}
    for move in moves:
        indexed.setdefault(move.product_id.id, move.env["stock.move"])
        indexed[move.product_id.id] |= move
    return indexed


def _apply_qty_rows(moves, rows, label, require_existing=True):
    """Apply [{product_id, quantity, lot_id?, lot_name?}] onto moves."""
    if rows is None:
        return None
    if not isinstance(rows, list):
        return error("INVALID_FIELDS", "%s must be an array." % label, 400)
    indexed = _index_moves_by_product(moves)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return error(
                "INVALID_FIELDS",
                "Each %s entry must be an object." % label,
                400,
            )
        extra = set(row) - {"product_id", "quantity", "lot_id", "lot_name", "move_id"}
        if extra:
            return error(
                "INVALID_FIELDS",
                "One or more %s fields are not allowed." % label,
                400,
            )
        product_id = strict_int(row.get("product_id"))
        move_id = strict_int(row.get("move_id"))
        qty, qty_err = _non_negative_finite_qty(row.get("quantity"), "quantity")
        if qty_err:
            return qty_err
        move = moves.browse()
        if move_id:
            move = moves.filtered(lambda candidate: candidate.id == move_id)[:1]
            if not move:
                return error(
                    "MOVE_NOT_FOUND",
                    "%s move_id=%s not found on manufacturing order." % (label, move_id),
                    404,
                )
            product_id = move.product_id.id
        if not product_id:
            return error(
                "INVALID_FIELDS",
                "product_id is required on each %s entry." % label,
                400,
            )
        if product_id in seen:
            return error(
                "DUPLICATE_LINE",
                "Duplicate product_id=%s in %s." % (product_id, label),
                400,
            )
        seen.add(product_id)
        if not move:
            move = indexed.get(product_id)
        if not move:
            if require_existing:
                code = (
                    "BYPRODUCT_NOT_ON_BOM"
                    if label == "byproducts"
                    else "COMPONENT_NOT_ON_MO"
                )
                return error(
                    code,
                    "Product %s is not on the manufacturing order %s."
                    % (product_id, label),
                    400,
                )
            return error(
                "MOVE_NOT_FOUND",
                "No stock move for product %s on this order." % product_id,
                404,
            )
        # One move per product expected for packing BoMs.
        move = move[:1]
        lot = move.env["stock.lot"]
        if move.product_id.tracking not in (False, "none"):
            lot, lot_err = _resolve_lot(
                move.env,
                move.product_id,
                lot_id=row.get("lot_id"),
                lot_name=row.get("lot_name"),
                company=move.company_id,
            )
            if lot_err:
                return lot_err
        try:
            _set_move_quantity(move, qty, lot=lot)
        except UserError as exc:
            return native_error("PACKING_QTY_FAILED", exc)
    return None


def _ensure_component_availability(production, warehouses):
    Quant = production.env["stock.quant"].sudo()
    for move in production.move_raw_ids.filtered(lambda row: row.state != "cancel"):
        qty = _move_done_qty(move)
        if float_is_zero(qty, precision_rounding=move.product_uom.rounding):
            continue
        if move.location_id.usage == "internal" and not location_in_warehouses(
            move.location_id, warehouses
        ):
            return error(
                "WAREHOUSE_NOT_ALLOWED",
                "Component location is outside assigned warehouses.",
                403,
            )
        # Include child internal locations under the move's warehouse root so
        # stock on shelves / sublocations remains usable.
        warehouse = move.location_id.warehouse_id
        if warehouse and warehouse in warehouses:
            location_domain = [("location_id", "child_of", warehouse.view_location_id.id)]
        else:
            location_domain = [("location_id", "child_of", move.location_id.id)]
        domain = [
            ("product_id", "=", move.product_id.id),
            ("quantity", "!=", 0),
        ] + location_domain
        lot = move.move_line_ids.filtered(lambda line: line.lot_id)[:1].lot_id
        if lot:
            domain.append(("lot_id", "=", lot.id))
        quants = Quant.search(domain)
        on_hand = float(sum(quants.mapped("quantity")))
        if float_compare(qty, on_hand, precision_rounding=move.product_uom.rounding) > 0:
            return error(
                "INSUFFICIENT_MATERIALS",
                "Insufficient stock for component %s (need %s, available %s)."
                % (move.product_id.display_name, qty, on_hand),
                409,
            )
    return None


def _process_scraps(production, scrap_rows, warehouses):
    if scrap_rows is None:
        return None
    if not isinstance(scrap_rows, list):
        return error("INVALID_FIELDS", "scraps must be an array.", 400)
    allowed_products = (
        production.move_raw_ids | production.move_finished_ids
    ).mapped("product_id")
    Scrap = production.env["stock.scrap"].sudo()
    for row in scrap_rows:
        if not isinstance(row, dict):
            return error("INVALID_FIELDS", "Each scraps entry must be an object.", 400)
        extra = set(row) - {"product_id", "quantity", "lot_id", "lot_name"}
        if extra:
            return error(
                "INVALID_FIELDS",
                "One or more scraps fields are not allowed.",
                400,
            )
        product_id = strict_int(row.get("product_id"))
        qty, qty_err = _positive_finite_qty(row.get("quantity"), "quantity")
        if not product_id or qty_err:
            return qty_err or error(
                "INVALID_FIELDS",
                "product_id and positive quantity are required on scraps.",
                400,
            )
        product = production.env["product.product"].sudo().browse(product_id).exists()
        if not product or product not in allowed_products:
            return error(
                "SCRAP_PRODUCT_NOT_ON_MO",
                "Scrap product must already exist on the manufacturing order.",
                400,
            )
        lot = production.env["stock.lot"]
        if product.tracking not in (False, "none"):
            lot, lot_err = _resolve_lot(
                production.env,
                product,
                lot_id=row.get("lot_id"),
                lot_name=row.get("lot_name"),
                company=production.company_id,
            )
            if lot_err:
                return lot_err
        # Prefer component source location; finished scrap uses dest.
        location = production.location_src_id
        if product == production.product_id or product in production.move_finished_ids.mapped(
            "product_id"
        ):
            if product not in production.move_raw_ids.mapped("product_id"):
                location = production.location_dest_id
        if not location_in_warehouses(location, warehouses):
            return error(
                "WAREHOUSE_NOT_ALLOWED",
                "Scrap location is outside assigned warehouses.",
                403,
            )
        vals = {
            "product_id": product.id,
            "product_uom_id": product.uom_id.id,
            "scrap_qty": qty,
            "production_id": production.id,
            "location_id": location.id,
            "company_id": production.company_id.id,
        }
        if lot:
            vals["lot_id"] = lot.id
        try:
            scrap = Scrap.create(vals)
            # action_validate posts scrap once into scrap location (no double MO consume).
            scrap_result = None
            if hasattr(scrap, "action_validate"):
                scrap_result = scrap.action_validate()
            elif hasattr(scrap, "do_scrap"):
                scrap_result = scrap.do_scrap()
            if isinstance(scrap_result, dict) and scrap_result.get("res_model") in (
                "stock.warn.insufficient.qty",
                "stock.warn.insufficient.qty.scrap",
            ):
                return error(
                    "INSUFFICIENT_MATERIALS",
                    "Insufficient stock to scrap %s." % product.display_name,
                    409,
                )
            scrap.invalidate_recordset()
            if scrap.state != "done":
                return error(
                    "SCRAP_FAILED",
                    "Scrap was not validated for product %s." % product.display_name,
                    400,
                )
        except (UserError, ValidationError) as exc:
            return native_error("SCRAP_FAILED", exc)
    return None


def _resolve_mark_done_result(production, result, create_backorder):
    """Resolve Odoo interactive wizards returned by button_mark_done."""
    for _ in range(8):
        production.invalidate_recordset()
        if production.state == "done":
            return None
        if result is True or result is None:
            return None
        if not isinstance(result, dict):
            return None

        model = result.get("res_model")
        action_type = result.get("type")
        # Post-done redirects / print actions are not stock wizards.
        if action_type == "ir.actions.client" or model in (False, None, "mrp.production"):
            return None

        ctx = dict(result.get("context") or {})
        Wizard = production.env[model].with_context(**ctx)
        if result.get("res_id"):
            wizard = Wizard.browse(result["res_id"])
        else:
            # Transient wizards from act_window often need create with context defaults.
            create_vals = {}
            if model == "mrp.production.backorder" and ctx.get("default_mrp_production_ids"):
                create_vals = {}
            wizard = Wizard.create(create_vals)

        if model == "mrp.consumption.warning":
            if wizard.consumption == "strict":
                return error(
                    "CONSUMPTION_STRICT",
                    "Component consumption differs from BoM and BoM consumption is strict.",
                    409,
                )
            result = wizard.action_confirm()
            continue
        if model == "mrp.production.backorder":
            if create_backorder:
                for line in wizard.mrp_production_backorder_line_ids:
                    line.to_backorder = True
                result = wizard.action_backorder()
            else:
                result = wizard.action_close_mo()
            continue
        if model in (
            "stock.warn.insufficient.qty",
            "stock.warn.insufficient.qty.scrap",
        ):
            return error(
                "INSUFFICIENT_MATERIALS",
                "Insufficient materials to complete manufacturing order.",
                409,
            )
        return error(
            "PACKING_WIZARD_UNSUPPORTED",
            "Manufacturing completion requires unsupported interactive step (%s)."
            % model,
            409,
        )
    production.invalidate_recordset()
    if production.state == "done":
        return None
    return error(
        "PACKING_WIZARD_LOOP",
        "Manufacturing completion wizard resolution exceeded safe iterations.",
        500,
    )


def complete_order(partner, production_id, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    err = require_perm(assignment, "perm_packing", "packing")
    if err:
        return err
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)

    allowed = {
        "finished_qty",
        "quantity",  # alias of finished_qty
        "finished_lot_id",
        "finished_lot_name",
        "create_backorder",
        "components",
        "byproducts",
        "scraps",
    }
    extra = set(payload) - allowed
    if extra:
        return error("INVALID_FIELDS", "One or more fields are not allowed.", 400)
    if "location_id" in payload or "location_dest_id" in payload:
        return error(
            "INVALID_FIELDS",
            "location_id and location_dest_id are not accepted.",
            400,
        )

    production, err = _get_accessible_production(partner, production_id, assignment)
    if err:
        return err

    warehouses = filter_assignment_warehouses(assignment)
    lock_err = _lock_production(production)
    if lock_err:
        return lock_err
    production.invalidate_recordset()

    if production.state == "done":
        return error(
            "PACKING_ORDER_ALREADY_DONE",
            "Manufacturing order is already done.",
            409,
        )
    if production.state == "cancel":
        return error(
            "PACKING_ORDER_CANCELLED",
            "Manufacturing order is cancelled.",
            409,
        )
    if production.state == "draft":
        return error(
            "PACKING_ORDER_NOT_CONFIRMED",
            "Confirm the manufacturing order before completing it.",
            409,
        )
    if production.state not in ("confirmed", "progress", "to_close"):
        return error(
            "PACKING_ORDER_INVALID_STATE",
            "Manufacturing order cannot be completed in state %s." % production.state,
            409,
        )

    warehouse = _warehouse_of_production(production, assignment)
    if not warehouse:
        return error(
            "WAREHOUSE_NOT_ALLOWED",
            "Manufacturing order warehouse is not assigned.",
            403,
        )

    raw_finished = (
        payload["finished_qty"]
        if "finished_qty" in payload
        else payload.get("quantity")
    )
    if raw_finished is None:
        return error(
            "INVALID_FIELDS",
            "finished_qty is required.",
            400,
        )
    finished_qty, qty_err = _positive_finite_qty(raw_finished, "finished_qty")
    if qty_err:
        return qty_err

    create_backorder = bool(payload.get("create_backorder"))

    try:
        with partner.env.cr.savepoint():
            # Finished product quantity for this completion.
            if production.product_id.tracking not in (False, "none"):
                lot, lot_err = _resolve_lot(
                    partner.env,
                    production.product_id,
                    lot_id=payload.get("finished_lot_id"),
                    lot_name=payload.get("finished_lot_name"),
                    company=production.company_id,
                )
                if lot_err:
                    raise _PackingAbort(lot_err)
                production.lot_producing_id = lot.id

            # Official Odoo path: qty_producing drives finished + proportional lines.
            production.qty_producing = finished_qty
            if hasattr(production, "set_qty_producing"):
                production.set_qty_producing()
            elif hasattr(production, "_set_qty_producing"):
                production._set_qty_producing()

            raw_moves = production.move_raw_ids.filtered(
                lambda move: move.state != "cancel"
            )
            finished_moves = production.move_finished_ids.filtered(
                lambda move: move.state != "cancel"
            )
            byproduct_moves = finished_moves.filtered(
                lambda move: bool(getattr(move, "byproduct_id", False))
                or move.product_id != production.product_id
            )
            main_moves = finished_moves.filtered(
                lambda move: move.product_id == production.product_id
                and not getattr(move, "byproduct_id", False)
            )
            if not main_moves:
                raise _PackingAbort(
                    error(
                        "PACKING_ORDER_NO_FINISHED_MOVE",
                        "Manufacturing order has no finished-product move to complete.",
                        409,
                    )
                )

            if "components" in payload:
                apply_err = _apply_qty_rows(
                    raw_moves, payload.get("components"), "components"
                )
                if apply_err:
                    raise _PackingAbort(apply_err)
            else:
                for move in raw_moves:
                    if "picked" in move._fields:
                        move.picked = True

            if "byproducts" in payload:
                apply_err = _apply_qty_rows(
                    byproduct_moves,
                    payload.get("byproducts"),
                    "byproducts",
                    require_existing=True,
                )
                if apply_err:
                    raise _PackingAbort(apply_err)
            else:
                for move in byproduct_moves:
                    if "picked" in move._fields and _move_done_qty(move) > 0:
                        move.picked = True

            # Do NOT manually force the main finished move quantity/picked flag.
            # Odoo _post_inventory sets it from qty_producing; touching the move
            # early can leave it unpicked and get it cancelled during mark_done.
            production.write({"qty_producing": finished_qty})
            if float_compare(
                float(production.qty_producing or 0.0),
                finished_qty,
                precision_rounding=production.product_uom_id.rounding,
            ) != 0:
                raise _PackingAbort(
                    error(
                        "PACKING_QTY_FAILED",
                        "Failed to persist finished_qty on the manufacturing order.",
                        500,
                    )
                )

            avail_err = _ensure_component_availability(production, warehouses)
            if avail_err:
                raise _PackingAbort(avail_err)

            # Multi-step manufacturing leaves component pickings open; raw moves
            # that are not picked are cancelled by _post_inventory and can cancel
            # the MO. Refuse instead of corrupting stock.
            warehouse_mo = production.warehouse_id or warehouse
            if (
                warehouse_mo
                and warehouse_mo.manufacture_steps != "mrp_one_step"
                and production.picking_ids.filtered(
                    lambda picking: picking.state not in ("done", "cancel")
                )
            ):
                raise _PackingAbort(
                    error(
                        "PACKING_PICKINGS_PENDING",
                        "Warehouse uses multi-step manufacturing. Finish component "
                        "pickings in Odoo (or use a one-step manufacturing warehouse) "
                        "before completing from the app.",
                        409,
                    )
                )

            production.flush_recordset()
            # Exact component/finished quantities were set by the API. Skip the
            # interactive consumption wizard; still resolve backorder when asked.
            mark_ctx = {
                "skip_redirection": True,
                "skip_consumption": True,
            }
            if not create_backorder:
                mark_ctx["skip_backorder"] = True
            result = production.with_context(**mark_ctx).button_mark_done()
            wizard_err = _resolve_mark_done_result(
                production, result, create_backorder=create_backorder
            )
            if wizard_err:
                raise _PackingAbort(wizard_err)

            production.invalidate_recordset()
            if production.state == "cancel":
                raise _PackingAbort(
                    error(
                        "PACKING_ORDER_CANCELLED",
                        "Manufacturing order was cancelled during completion.",
                        409,
                    )
                )
            if production.state != "done":
                raise _PackingAbort(
                    error(
                        "PACKING_ORDER_NOT_DONE",
                        "Manufacturing order was not marked done by Odoo (state=%s)."
                        % production.state,
                        500,
                    )
                )

            # Scraps post once via stock.scrap after MO is done so they do not
            # disturb reservations / mark_done wizards. Do not also include the
            # same physical units inside components[].
            scrap_err = _process_scraps(
                production, payload.get("scraps"), warehouses
            )
            if scrap_err:
                raise _PackingAbort(scrap_err)
    except _PackingAbort as abort:
        return abort.response
    except (UserError, ValidationError) as exc:
        return native_error("PACKING_ORDER_COMPLETE_FAILED", exc)

    return success(
        serialize_production(production, detail=True, warehouses=warehouses)
    )
