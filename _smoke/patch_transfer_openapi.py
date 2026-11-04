import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.78.0"
desc = doc["info"].get("description") or ""
for old in ("17.0.1.75.0", "17.0.1.74.0", "17.0.1.73.0"):
    desc = desc.replace(old, "17.0.1.78.0")
doc["info"]["description"] = desc

paths = doc["paths"]
paths.pop("/almohit/api/warehouse/transfers/{transfer_id}/dispatch", None)

create = paths.get("/almohit/api/warehouse/transfers", {}).get("post")
if create:
    create["summary"] = "Create transfer and dispatch to transit"
    create["description"] = (
        "Creates an inter-warehouse transfer and immediately moves stock from "
        "source lot_stock to Transit in one atomic request. Returns state "
        "dispatched (not complete). Destination receive remains "
        "POST /transfers/{id}/receive. Optional idempotency_key returns the "
        "existing transfer on retry. Source and destination warehouses must "
        "both be assigned. No location_id / location_dest_id."
    )
    create["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/WarehouseTransferCreate"},
                "example": {
                    "source_warehouse_id": 1,
                    "dest_warehouse_id": 2,
                    "idempotency_key": "wh-tr-2026-001",
                    "lines": [{"product_id": 158, "quantity": 5}],
                },
            }
        },
    }
    create["responses"] = {
        "201": {"description": "created and dispatched (state=dispatched)"},
        "200": {"description": "idempotent replay of existing transfer"},
        "400": {"description": "INVALID_FIELDS | WAREHOUSE_CONFIG"},
        "403": {"description": "WAREHOUSE_NOT_ALLOWED | WAREHOUSE_PERMISSION_DENIED"},
        "404": {"description": "PRODUCT_NOT_FOUND | WAREHOUSE_NOT_FOUND"},
        "409": {
            "description": (
                "INSUFFICIENT_STOCK | TRANSFER_BUSY | TRANSFER_DUPLICATE | "
                "TRANSFER_DISPATCH_FAILED"
            )
        },
        "401": {"description": "AUTHENTICATION_REQUIRED"},
    }

schemas = doc.setdefault("components", {}).setdefault("schemas", {})
schemas["WarehouseTransferCreate"] = {
    "type": "object",
    "required": ["source_warehouse_id", "dest_warehouse_id", "lines"],
    "additionalProperties": False,
    "properties": {
        "source_warehouse_id": {"type": "integer", "minimum": 1},
        "dest_warehouse_id": {"type": "integer", "minimum": 1},
        "note": {"type": "string"},
        "idempotency_key": {
            "type": "string",
            "description": "Optional. Unique per company; retries return the same transfer.",
        },
        "lines": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["product_id", "quantity"],
                "additionalProperties": False,
                "properties": {
                    "product_id": {"type": "integer", "minimum": 1},
                    "quantity": {"type": "number", "exclusiveMinimum": 0},
                    "uom_id": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
}

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("openapi patched")
