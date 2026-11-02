import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.71.0"
desc = doc["info"].get("description") or ""
for old in ("17.0.1.70.0", "17.0.1.69.0", "17.0.1.68.0"):
    desc = desc.replace(old, "17.0.1.71.0")
doc["info"]["description"] = desc

paths = doc["paths"]
for key in list(paths):
    if key.endswith("/issues/{picking_id}/source"):
        del paths[key]
        print("removed", key)

dispatch = paths.get("/almohit/api/warehouse/issues/{picking_id}/dispatch")
if dispatch and "post" in dispatch:
    dispatch["post"]["summary"] = "Dispatch goods with per-line warehouse"
    dispatch["post"]["description"] = (
        "Requires issue permission. Every line must include move_id, quantity_done, "
        "and warehouse_id from assigned warehouses. Location fields and request-level "
        "warehouse_id are rejected. Lines for different warehouses each create a native "
        "stock→output internal picking for that warehouse, then customer delivery moves "
        "are relocated to the matching Output. Customer delivery is not validated and "
        "distributor user_id is untouched. Response includes result_pickings."
    )
    dispatch["post"]["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/WarehouseDispatchRequest"}
            }
        },
    }
    dispatch["post"]["responses"] = {
        "200": {
            "description": (
                "ok:true with lines (warehouse per product), "
                "result_pickings, dispatch_state=dispatched, sale_order"
            )
        },
        "400": {
            "description": "INVALID_FIELDS | INVALID_QUANTITY | DUPLICATE_MOVE | WAREHOUSE_CONFIG"
        },
        "403": {
            "description": "WAREHOUSE_PERMISSION_DENIED | WAREHOUSE_NOT_ALLOWED | PAYMENT_REQUIRED"
        },
        "409": {
            "description": (
                "ISSUE_ALREADY_DONE | ISSUE_ALREADY_DISPATCHED | ISSUE_BUSY | "
                "INSUFFICIENT_STOCK | ISSUE_DISPATCH_INCOMPLETE"
            )
        },
    }

detail = paths.get("/almohit/api/warehouse/issues/{picking_id}")
if detail and "get" in detail:
    detail["get"]["description"] = (
        "Detail includes lines (move_id, qtys) and available_warehouses for per-line "
        "dispatch. current_warehouse / suggested_warehouse are informational only and "
        "are not required for POST dispatch."
    )

schemas = doc.setdefault("components", {}).setdefault("schemas", {})
schemas["WarehouseDispatchRequest"] = {
    "type": "object",
    "required": ["lines"],
    "additionalProperties": False,
    "description": (
        "Per-line warehouse dispatch. Every line requires move_id, quantity_done, "
        "and warehouse_id. Rejects location fields and request-level warehouse_id. "
        "Same contract shape as WarehouseReceiveRequest."
    ),
    "properties": {
        "lines": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["move_id", "quantity_done", "warehouse_id"],
                "additionalProperties": False,
                "properties": {
                    "move_id": {"type": "integer", "minimum": 1},
                    "quantity_done": {"type": "number", "exclusiveMinimum": 0},
                    "warehouse_id": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Assigned warehouse for this line only.",
                    },
                },
            },
        }
    },
    "example": {
        "lines": [
            {"move_id": 271, "quantity_done": 3, "warehouse_id": 2},
            {"move_id": 272, "quantity_done": 5, "warehouse_id": 1},
        ]
    },
}
schemas.pop("WarehouseChangeIssueSourceRequest", None)

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("openapi patched", doc["info"]["version"])
