import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.75.0"
desc = doc["info"].get("description") or ""
for old in ("17.0.1.74.0", "17.0.1.73.0", "17.0.1.72.0"):
    desc = desc.replace(old, "17.0.1.75.0")
doc["info"]["description"] = desc

paths = doc["paths"]
paths["/almohit/api/warehouse/distributors"] = {
    "get": {
        "tags": ["Warehouse"],
        "summary": "List assignable distributors",
        "description": (
            "Requires warehouse manager + issue permission. Returns active approved "
            "users with almohit_app_role=distributor (id, name only)."
        ),
        "security": [{"sessionCookie": []}],
        "responses": {
            "200": {"description": "ok:true with items[{id, name}]"},
            "401": {"description": "AUTHENTICATION_REQUIRED"},
            "403": {"description": "WAREHOUSE_PERMISSION_DENIED | WAREHOUSE_ASSIGNMENT_REQUIRED"},
        },
    }
}

dispatch = paths.get("/almohit/api/warehouse/issues/{picking_id}/dispatch", {})
post = dispatch.get("post")
if post:
    post["description"] = (
        "Requires issue permission. Every line must include move_id, quantity_done, "
        "and warehouse_id. Optional distributor_user_id assigns stock.picking.user_id "
        "(native Odoo Responsible). Omit to leave assignee unchanged; null/0 rejected. "
        "Location fields and request-level warehouse_id are rejected. Payment checks "
        "still run on user_id write. Response includes distributor_user_id and result_pickings."
    )
    post["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["lines"],
                    "additionalProperties": False,
                    "properties": {
                        "distributor_user_id": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Optional. res.users id of an approved distributor. "
                                "Maps to stock.picking.user_id."
                            ),
                        },
                        "lines": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": ["move_id", "quantity_done", "warehouse_id"],
                                "additionalProperties": False,
                                "properties": {
                                    "move_id": {"type": "integer", "minimum": 1},
                                    "quantity_done": {
                                        "type": "number",
                                        "exclusiveMinimum": 0,
                                    },
                                    "warehouse_id": {"type": "integer", "minimum": 1},
                                },
                            },
                        },
                    },
                    "example": {
                        "distributor_user_id": 275,
                        "lines": [
                            {
                                "move_id": 271,
                                "quantity_done": 3,
                                "warehouse_id": 1,
                            }
                        ],
                    },
                }
            }
        },
    }

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("openapi patched", doc["info"]["version"])
