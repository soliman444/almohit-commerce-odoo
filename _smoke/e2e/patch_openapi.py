import json
from pathlib import Path

p = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(p.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.57.0"
desc = doc["info"]["description"]
needle = "Distributor: delivery APIs (approved distributor required)."
insert = (
    needle
    + " Warehouse Manager: `/almohit/api/warehouse/*` (approved + warehouse assignment + permission flags; see warehouse_api.md)."
)
if "Warehouse Manager:" not in desc:
    doc["info"]["description"] = desc.replace(needle, insert)

if not any(t.get("name") == "Warehouse" for t in doc["tags"]):
    doc["tags"].append(
        {
            "name": "Warehouse",
            "description": (
                "Warehouse manager stock, receipts, issues, transfers, inventory. "
                "Requires role warehouse_manager, approved trade status, and "
                "almohit.warehouse.assignment. Receipt mode ICP "
                "almohit.receipt_validation_mode: both|warehouse_only|buyer_only."
            ),
        }
    )

comp = doc.setdefault("components", {}).setdefault("schemas", {})
comp["WarehouseAccess"] = {
    "type": "object",
    "properties": {
        "warehouses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        },
        "default_warehouse_id": {"type": "integer", "nullable": True},
        "permissions": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "stock_read",
                    "receipt",
                    "issue",
                    "transfer_create",
                    "transfer_receive",
                ],
            },
        },
    },
}
comp["WarehouseReceiveRequest"] = {
    "type": "object",
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "move_id": {"type": "integer"},
                    "quantity_done": {"type": "number"},
                    "location_dest_id": {"type": "integer"},
                    "destinations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["location_id", "quantity"],
                            "properties": {
                                "location_id": {"type": "integer"},
                                "quantity": {"type": "number"},
                            },
                        },
                    },
                },
            },
        }
    },
}
comp["WarehouseDispatchRequest"] = {
    "type": "object",
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["move_id", "quantity_done"],
                "properties": {
                    "move_id": {"type": "integer"},
                    "quantity_done": {"type": "number"},
                    "location_id": {"type": "integer"},
                },
            },
        }
    },
}
comp["WarehouseTransferCreate"] = {
    "type": "object",
    "required": ["source_warehouse_id", "dest_warehouse_id", "lines"],
    "properties": {
        "source_warehouse_id": {"type": "integer"},
        "dest_warehouse_id": {"type": "integer"},
        "note": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["product_id", "quantity"],
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "number"},
                    "location_id": {"type": "integer"},
                },
            },
        },
    },
}
comp["WarehouseInventoryImport"] = {
    "type": "object",
    "required": ["warehouse_id", "file_base64"],
    "properties": {
        "warehouse_id": {"type": "integer"},
        "session_type": {
            "type": "string",
            "enum": ["opening", "count"],
            "default": "opening",
        },
        "filename": {"type": "string"},
        "file_base64": {"type": "string"},
        "note": {"type": "string"},
    },
}

AUTH = [{"sessionCookie": []}]
COMMON = {
    "401": {"description": "AUTHENTICATION_REQUIRED"},
    "403": {
        "description": (
            "ACCESS_DENIED | WAREHOUSE_APPROVAL_REQUIRED | "
            "WAREHOUSE_ASSIGNMENT_REQUIRED | WAREHOUSE_PERMISSION_DENIED | "
            "CROSS_COMPANY_DENIED | RECEIPT_BUYER_ONLY | RECEIPT_HANDED_TO_WAREHOUSE"
        )
    },
}


def get_op(summary, params=None):
    return {
        "tags": ["Warehouse"],
        "summary": summary,
        "security": AUTH,
        "parameters": params or [],
        "responses": {"200": {"description": "ok:true envelope"}, **COMMON},
    }


def post_op(summary, schema_ref=None):
    op = {
        "tags": ["Warehouse"],
        "summary": summary,
        "security": AUTH,
        "responses": {
            "200": {"description": "ok:true"},
            "201": {"description": "created"},
            "400": {"description": "validation"},
            "404": {"description": "not found"},
            "409": {
                "description": (
                    "IMPORT_DUPLICATE | RECEIPT_ALREADY_DONE | "
                    "ISSUE_ALREADY_DISPATCHED | TRANSFER_NEEDS_RECONCILE | ..."
                )
            },
            **COMMON,
        },
    }
    if schema_ref:
        op["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": schema_ref},
                }
            },
        }
    return op


pid = {
    "name": "picking_id",
    "in": "path",
    "required": True,
    "schema": {"type": "integer"},
}
tid = {
    "name": "transfer_id",
    "in": "path",
    "required": True,
    "schema": {"type": "integer"},
}
sid = {
    "name": "session_id",
    "in": "path",
    "required": True,
    "schema": {"type": "integer"},
}
did = {
    "name": "discrepancy_id",
    "in": "path",
    "required": True,
    "schema": {"type": "integer"},
}
page = {"$ref": "#/components/parameters/Page"}
psize = {"$ref": "#/components/parameters/PageSize"}

paths = {
    "/almohit/api/warehouse/dashboard": {"get": get_op("Dashboard KPIs")},
    "/almohit/api/warehouse/my-warehouses": {
        "get": get_op("Assigned warehouses + permissions")
    },
    "/almohit/api/warehouse/stock": {
        "get": get_op(
            "Stock quants",
            [
                {
                    "name": "warehouse_id",
                    "in": "query",
                    "schema": {"type": "integer"},
                },
                {
                    "name": "product_id",
                    "in": "query",
                    "schema": {"type": "integer"},
                },
                page,
                psize,
            ],
        )
    },
    "/almohit/api/warehouse/movements": {
        "get": get_op(
            "Stock movements",
            [
                {"$ref": "#/components/parameters/DateFrom"},
                {"$ref": "#/components/parameters/DateTo"},
                page,
                psize,
            ],
        )
    },
    "/almohit/api/warehouse/receipts": {
        "get": get_op(
            "List receipts",
            [
                {"name": "status", "in": "query", "schema": {"type": "string"}},
                {
                    "name": "kind",
                    "in": "query",
                    "schema": {"type": "string", "enum": ["purchase", "return"]},
                },
                page,
                psize,
            ],
        )
    },
    "/almohit/api/warehouse/receipts/{picking_id}": {
        "get": {**get_op("Receipt detail"), "parameters": [pid]}
    },
    "/almohit/api/warehouse/receipts/{picking_id}/receive": {
        "post": {
            **post_op(
                "Receive goods (split destinations / partial → backorder)",
                "#/components/schemas/WarehouseReceiveRequest",
            ),
            "parameters": [pid],
        }
    },
    "/almohit/api/warehouse/issues": {
        "get": get_op(
            "List sales issues",
            [
                {
                    "name": "status",
                    "in": "query",
                    "schema": {
                        "type": "string",
                        "enum": ["pending", "dispatched", "done", "all"],
                    },
                },
                page,
                psize,
            ],
        )
    },
    "/almohit/api/warehouse/issues/{picking_id}": {
        "get": {**get_op("Issue detail"), "parameters": [pid]}
    },
    "/almohit/api/warehouse/issues/{picking_id}/dispatch": {
        "post": {
            **post_op(
                "Dispatch Stock→Output without completing customer delivery",
                "#/components/schemas/WarehouseDispatchRequest",
            ),
            "parameters": [pid],
        }
    },
    "/almohit/api/warehouse/transfers": {
        "get": get_op(
            "List transfers",
            [
                {
                    "name": "direction",
                    "in": "query",
                    "schema": {
                        "type": "string",
                        "enum": ["incoming", "outgoing", "all"],
                    },
                },
                {"name": "status", "in": "query", "schema": {"type": "string"}},
                page,
                psize,
            ],
        ),
        "post": post_op(
            "Create inter-warehouse transfer",
            "#/components/schemas/WarehouseTransferCreate",
        ),
    },
    "/almohit/api/warehouse/transfers/{transfer_id}": {
        "get": {**get_op("Transfer detail"), "parameters": [tid]}
    },
    "/almohit/api/warehouse/transfers/{transfer_id}/dispatch": {
        "post": {**post_op("Dispatch to transit"), "parameters": [tid]}
    },
    "/almohit/api/warehouse/transfers/{transfer_id}/receive": {
        "post": {
            **post_op("Receive from transit (partial/discrepancy)"),
            "parameters": [tid],
        }
    },
    "/almohit/api/warehouse/transfers/{transfer_id}/cancel": {
        "post": {**post_op("Cancel draft/requested"), "parameters": [tid]}
    },
    "/almohit/api/warehouse/transfers/{transfer_id}/reject": {
        "post": {
            **post_op("Reject before dispatch (else TRANSFER_NEEDS_RECONCILE)"),
            "parameters": [tid],
        }
    },
    "/almohit/api/warehouse/transfers/{transfer_id}/close": {
        "post": {**post_op("Close after receive/reconcile"), "parameters": [tid]}
    },
    "/almohit/api/warehouse/discrepancies/{discrepancy_id}/resolve": {
        "post": {**post_op("Resolve discrepancy"), "parameters": [did]}
    },
    "/almohit/api/warehouse/inventory": {
        "get": get_op("List inventory sessions", [page, psize])
    },
    "/almohit/api/warehouse/inventory/{session_id}": {
        "get": {**get_op("Inventory session detail"), "parameters": [sid]}
    },
    "/almohit/api/warehouse/inventory/import": {
        "post": post_op(
            "Import CSV/XLSX preview (checksum dedupe)",
            "#/components/schemas/WarehouseInventoryImport",
        )
    },
    "/almohit/api/warehouse/inventory/{session_id}/approve": {
        "post": {**post_op("Approve and apply counted quantities"), "parameters": [sid]}
    },
}

doc.setdefault("paths", {}).update(paths)
p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("warehouse paths", sum(1 for k in doc["paths"] if "/warehouse/" in k))
print("version", doc["info"]["version"])
