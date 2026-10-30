import hashlib
import base64
import csv
import io
import zipfile
import xml.etree.ElementTree as ET

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import UserError, ValidationError

from .api import error, paginated_success, parse_pagination, success, _query_scalar
from .warehouse_common import (
    assert_same_company,
    assignment_or_error,
    filter_assignment_warehouses,
    location_in_warehouses,
    named,
    native_error,
    require_perm,
    strict_float,
    strict_int,
)


def serialize_session(session, detail=False):
    item = {
        "id": session.id,
        "name": session.name,
        "state": session.state,
        "session_type": session.session_type,
        "warehouse": named(session.warehouse_id),
        "location": named(session.location_id),
        "source_filename": session.source_filename or None,
        "created_by": named(session.created_by_id),
        "approved_by": named(session.approved_by_id),
        "approved_at": session.approved_at or None,
        "line_count": len(session.line_ids),
    }
    if detail:
        item["lines"] = [{
            "id": line.id,
            "row_number": line.row_number,
            "product": {
                "id": line.product_id.id,
                "name": line.product_id.display_name,
                "default_code": line.product_id.default_code or None,
                "barcode": line.product_id.barcode or None,
            },
            "uom": named(line.product_uom_id),
            "qty_on_hand": float(line.qty_on_hand or 0.0),
            "qty_counted": float(line.qty_counted or 0.0),
            "qty_difference": float(line.qty_difference or 0.0),
            "note": line.note or None,
        } for line in session.line_ids]
        item["note"] = session.note or None
    return item


def list_sessions(partner, kwargs):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    page, page_size, page_err = parse_pagination(kwargs)
    if page_err:
        return page_err
    domain = [
        ("company_id", "=", partner.env.company.id),
        ("warehouse_id", "in", assignment.warehouse_ids.ids),
    ]
    status = (_query_scalar(kwargs, "status") or "").strip().lower()
    if status:
        domain.append(("state", "=", status))
    session_type = (_query_scalar(kwargs, "session_type") or "").strip().lower()
    if session_type in ("count", "opening"):
        domain.append(("session_type", "=", session_type))
    sessions = partner.env["almohit.inventory.session"].sudo().search(domain, order="id desc")
    return paginated_success(sessions, serialize_session, page, page_size)


def get_session(partner, session_id):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    session = partner.env["almohit.inventory.session"].sudo().browse(session_id).exists()
    if not session or session.warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("INVENTORY_NOT_FOUND", "Inventory session not found.", 404)
    return success(serialize_session(session, detail=True))


def _resolve_product(env, code=None, barcode=None, product_id=None):
    Product = env["product.product"].sudo()
    if product_id:
        product = Product.browse(product_id).exists()
        return product if product else Product.browse()
    if barcode:
        product = Product.search([("barcode", "=", barcode)], limit=1)
        if product:
            return product
    if code:
        product = Product.search([("default_code", "=", code)], limit=1)
        if product:
            return product
    return Product.browse()


def _parse_csv_rows(raw_csv):
    text = raw_csv
    if isinstance(raw_csv, bytes):
        text = raw_csv.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader, start=2):
        rows.append((index, { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }))
    return rows


def _col_letter_to_index(cell_ref):
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    value = 0
    for ch in letters.upper():
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def _parse_xlsx_rows(raw_bytes):
    """Minimal XLSX reader (first sheet) without openpyxl dependency."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                texts = [node.text or "" for node in si.iterfind(".//m:t", ns)]
                shared.append("".join(texts))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in archive.namelist():
            sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
            if not sheets:
                raise ValueError("No worksheet found in xlsx")
            sheet_name = sorted(sheets)[0]
        root = ET.fromstring(archive.read(sheet_name))
        matrix = {}
        max_col = 0
        max_row = 0
        for row in root.findall("m:sheetData/m:row", ns):
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r") or ""
                col = _col_letter_to_index(ref)
                row_idx = int("".join(ch for ch in ref if ch.isdigit()) or "0")
                cell_type = cell.attrib.get("t")
                raw_value = ""
                if cell_type == "inlineStr":
                    texts = [node.text or "" for node in cell.iterfind(".//m:t", ns)]
                    raw_value = "".join(texts)
                else:
                    value_node = cell.find("m:v", ns)
                    raw_value = value_node.text if value_node is not None else ""
                    if cell_type == "s":
                        try:
                            raw_value = shared[int(raw_value)]
                        except (ValueError, IndexError):
                            raw_value = ""
                matrix[(row_idx, col)] = (raw_value or "").strip()
                max_col = max(max_col, col)
                max_row = max(max_row, row_idx)
        if max_row < 1:
            return []
        headers = [
            (matrix.get((1, col)) or "").strip().lower()
            for col in range(0, max_col + 1)
        ]
        rows = []
        for row_idx in range(2, max_row + 1):
            values = {
                headers[col]: matrix.get((row_idx, col), "")
                for col in range(0, max_col + 1)
                if headers[col]
            }
            if any(values.values()):
                rows.append((row_idx, values))
        return rows


def _parse_import_rows(raw_bytes, filename):
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return _parse_xlsx_rows(raw_bytes)
    # sniff zip/xlsx even without extension
    if raw_bytes[:2] == b"PK":
        try:
            return _parse_xlsx_rows(raw_bytes)
        except Exception:
            pass
    return _parse_csv_rows(raw_bytes)


def preview_import(partner, payload):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    # Reuse stock_read or require receipt/issue for inventory? Use stock_read + future inventory perm.
    # Spec: restrict approval to authorized managers — use perm_receipt as stand-in OR allow any assigned with stock_read for preview.
    if not assignment.perm_stock_read:
        return error("WAREHOUSE_PERMISSION_DENIED", "Missing permission: stock_read.", 403)
    if not isinstance(payload, dict):
        return error("INVALID_JSON", "Invalid JSON body.", 400)

    warehouse_id = strict_int(payload.get("warehouse_id"))
    location_id = strict_int(payload.get("location_id"))
    session_type = (payload.get("session_type") or "count").strip().lower()
    if session_type not in ("count", "opening"):
        return error("INVALID_FIELDS", "session_type must be count or opening.", 400)
    company_warehouses = filter_assignment_warehouses(assignment)
    if not warehouse_id or warehouse_id not in company_warehouses.ids:
        return error("WAREHOUSE_NOT_ALLOWED", "Warehouse is not assigned.", 403)
    warehouse = partner.env["stock.warehouse"].sudo().browse(warehouse_id)
    company_err = assert_same_company(warehouse, partner.env.company)
    if company_err:
        return company_err
    location = (
        partner.env["stock.location"].sudo().browse(location_id).exists()
        if location_id
        else warehouse.lot_stock_id
    )
    if not location_in_warehouses(location, warehouse):
        return error("WAREHOUSE_NOT_ALLOWED", "Location is outside warehouse.", 403)

    file_b64 = payload.get("file_base64")
    filename = (payload.get("filename") or "import.csv").strip()
    if not file_b64:
        return error("INVALID_FIELDS", "file_base64 is required.", 400)
    try:
        raw = base64.b64decode(file_b64)
    except Exception:
        return error("INVALID_FIELDS", "file_base64 is invalid.", 400)
    checksum = hashlib.sha256(raw).hexdigest()
    existing = partner.env["almohit.inventory.session"].sudo().search([
        ("company_id", "=", partner.env.company.id),
        ("import_checksum", "=", checksum),
        ("state", "!=", "cancelled"),
    ], limit=1)
    if existing:
        return error(
            "IMPORT_DUPLICATE",
            "This import file was already uploaded as %s." % existing.name,
            409,
            extra={"session_id": existing.id},
        )

    try:
        rows = _parse_import_rows(raw, filename)
    except Exception:
        return error(
            "INVALID_IMPORT",
            "Could not parse import file. Use CSV or XLSX with headers: default_code|barcode,quantity.",
            400,
        )
    if not rows:
        return error("INVALID_IMPORT", "Import file has no data rows.", 400)

    Quant = partner.env["stock.quant"].sudo()
    preview_lines = []
    errors = []
    seen_products = set()
    for row_number, row in rows:
        code = row.get("default_code") or row.get("sku") or row.get("code")
        barcode = row.get("barcode")
        qty_text = row.get("quantity") or row.get("qty") or row.get("counted_qty")
        qty = strict_float(qty_text)
        product = _resolve_product(partner.env, code=code, barcode=barcode)
        if not product:
            errors.append({"row": row_number, "error": "PRODUCT_NOT_FOUND", "code": code or barcode})
            continue
        if product.id in seen_products:
            errors.append({"row": row_number, "error": "DUPLICATE_PRODUCT", "product_id": product.id})
            continue
        if qty is None or qty < 0:
            errors.append({"row": row_number, "error": "INVALID_QUANTITY"})
            continue
        seen_products.add(product.id)
        on_hand = Quant._get_available_quantity(product, location) if hasattr(Quant, "_get_available_quantity") else 0.0
        # Prefer quantity (on hand) not only available
        quants = Quant.search([
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ])
        on_hand = float(sum(quants.mapped("quantity")) if quants else on_hand or 0.0)
        preview_lines.append({
            "row_number": row_number,
            "product_id": product.id,
            "default_code": product.default_code or None,
            "barcode": product.barcode or None,
            "name": product.display_name,
            "uom_id": product.uom_id.id,
            "qty_on_hand": on_hand,
            "qty_counted": qty,
            "qty_difference": qty - on_hand,
        })

    if errors and not preview_lines:
        return error("INVALID_IMPORT", "Import validation failed.", 400, extra={"errors": errors})

    session = partner.env["almohit.inventory.session"].sudo().create({
        "session_type": session_type,
        "warehouse_id": warehouse.id,
        "location_id": location.id,
        "state": "preview",
        "import_checksum": checksum,
        "source_filename": filename,
        "note": (payload.get("note") or "").strip() or False,
        "line_ids": [(0, 0, {
            "product_id": line["product_id"],
            "product_uom_id": line["uom_id"],
            "qty_on_hand": line["qty_on_hand"],
            "qty_counted": line["qty_counted"],
            "row_number": line["row_number"],
        }) for line in preview_lines],
    })
    data = serialize_session(session, detail=True)
    data["validation_errors"] = errors
    return success(data, status=201)


def approve_session(partner, session_id, payload=None):
    assignment, err = assignment_or_error(partner)
    if err:
        return err
    # Approval requires receipt or issue permission as manager gate.
    if not (assignment.perm_receipt or assignment.perm_issue):
        return error(
            "WAREHOUSE_PERMISSION_DENIED",
            "Inventory approval requires receipt or issue permission.",
            403,
        )
    session = partner.env["almohit.inventory.session"].sudo().browse(session_id).exists()
    if not session or session.warehouse_id.id not in assignment.warehouse_ids.ids:
        return error("INVENTORY_NOT_FOUND", "Inventory session not found.", 404)
    if session.state != "preview":
        return error("INVENTORY_INVALID_STATE", "Only preview sessions can be approved.", 409)
    if not session.line_ids:
        return error("INVENTORY_EMPTY", "Session has no lines.", 400)

    # Apply as superuser after auth/scope checks — portal WH managers lack
    # stock.group_stock_manager which native action_apply_inventory requires.
    Quant = partner.env["stock.quant"].with_user(SUPERUSER_ID).sudo()
    try:
        for line in session.line_ids:
            quant = Quant.search([
                ("product_id", "=", line.product_id.id),
                ("location_id", "=", session.location_id.id),
                ("lot_id", "=", False),
                ("package_id", "=", False),
                ("owner_id", "=", False),
            ], limit=1)
            if not quant:
                quant = Quant.create({
                    "product_id": line.product_id.id,
                    "location_id": session.location_id.id,
                    "inventory_quantity": float(line.qty_counted or 0.0),
                })
            else:
                quant.inventory_quantity = float(line.qty_counted or 0.0)
            quant.with_user(SUPERUSER_ID).sudo().action_apply_inventory()
    except (UserError, ValidationError) as exc:
        return native_error("INVENTORY_APPLY_FAILED", exc)

    session.write({
        "state": "approved",
        "approved_by_id": partner.env.user.id,
        "approved_at": fields.Datetime.now(),
    })
    return success(serialize_session(session, detail=True))
