import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
schemas = doc.setdefault("components", {}).setdefault("schemas", {})

schemas["WarehouseReceiveRequest"] = {
    "type": "object",
    "required": ["lines"],
    "additionalProperties": False,
    "description": (
        "Per-line warehouse receive. Every line requires move_id, quantity_done, "
        "and warehouse_id. Rejects location fields and request-level warehouse_id."
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
            {"move_id": 263, "quantity_done": 10, "warehouse_id": 1},
            {"move_id": 264, "quantity_done": 20, "warehouse_id": 2},
        ]
    },
}

schemas.pop("WarehouseChangeReceiptDestinationRequest", None)
schemas.pop("WarehouseChangeReceiptWarehouseRequest", None)

# Ensure POST receive requestBody references the component when possible
recv = doc["paths"].get("/almohit/api/warehouse/receipts/{picking_id}/receive", {})
post = recv.get("post")
if post:
    post["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/WarehouseReceiveRequest"}
            }
        },
    }

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("schemas updated; removed destination schema")
