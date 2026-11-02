import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.70.0"
desc = doc["info"].get("description") or ""
for old in ("17.0.1.69.0", "17.0.1.68.0", "17.0.1.67.0"):
    desc = desc.replace(old, "17.0.1.70.0")
doc["info"]["description"] = desc

paths = doc["paths"]
for key in list(paths):
    if "/warehouse/receipts/" in key and (
        key.endswith("/destination") or key.endswith("/warehouse")
    ):
        del paths[key]
        print("removed", key)

recv = paths.get("/almohit/api/warehouse/receipts/{picking_id}/receive")
if recv and "post" in recv:
    recv["post"]["summary"] = "Receive goods with per-line warehouse"
    recv["post"]["description"] = (
        "Requires receipt permission. Every line must include move_id, quantity_done, "
        "and warehouse_id from assigned warehouses. Location fields and request-level "
        "warehouse_id are rejected. Lines for different warehouses are split onto native "
        "incoming pickings per warehouse operation type, then validated atomically. "
        "Response includes result_pickings and backorders."
    )
    recv["post"]["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["lines"],
                    "additionalProperties": False,
                    "properties": {
                        "lines": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": [
                                    "move_id",
                                    "quantity_done",
                                    "warehouse_id",
                                ],
                                "additionalProperties": False,
                                "properties": {
                                    "move_id": {
                                        "type": "integer",
                                        "minimum": 1,
                                    },
                                    "quantity_done": {
                                        "type": "number",
                                        "exclusiveMinimum": 0,
                                    },
                                    "warehouse_id": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "description": (
                                            "Assigned warehouse for this line only."
                                        ),
                                    },
                                },
                            },
                        }
                    },
                    "example": {
                        "lines": [
                            {
                                "move_id": 263,
                                "quantity_done": 10,
                                "warehouse_id": 1,
                            },
                            {
                                "move_id": 264,
                                "quantity_done": 20,
                                "warehouse_id": 2,
                            },
                        ]
                    },
                }
            }
        },
    }
    recv["post"]["responses"] = {
        "200": {
            "description": (
                "ok:true with lines (warehouse per product), "
                "result_pickings, backorders"
            )
        },
        "400": {
            "description": "INVALID_FIELDS | INVALID_QUANTITY | DUPLICATE_MOVE"
        },
        "403": {
            "description": "WAREHOUSE_PERMISSION_DENIED | WAREHOUSE_NOT_ALLOWED"
        },
        "409": {
            "description": (
                "RECEIPT_ALREADY_DONE | RECEIPT_BUSY | RECEIPT_INCOMPLETE | "
                "RECEIPT_LINE_ALREADY_DONE"
            )
        },
    }

detail = paths.get("/almohit/api/warehouse/receipts/{picking_id}")
if detail and "get" in detail:
    detail["get"]["description"] = (
        "Detail includes lines (move_id, qtys) and available_warehouses for per-line "
        "receive. current_warehouse / suggested_warehouse are informational only and "
        "are not required for POST receive."
    )

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("openapi patched", doc["info"]["version"])
