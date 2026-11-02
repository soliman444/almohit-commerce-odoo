import json
from pathlib import Path

path = Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs\openapi.json")
doc = json.loads(path.read_text(encoding="utf-8"))
doc["info"]["version"] = "17.0.1.72.0"
desc = doc["info"].get("description") or ""
for old in ("17.0.1.71.0", "17.0.1.70.0"):
    desc = desc.replace(old, "17.0.1.72.0")
doc["info"]["description"] = desc

stock = doc["paths"].get("/almohit/api/warehouse/stock", {})
if stock.get("get"):
    stock["get"]["summary"] = "Stock quants with warehouse and stock_role"
    stock["get"]["description"] = (
        "Lists non-zero quants under assigned warehouses (including archived "
        "Input/Output/Pack). Each item includes stock_role inferred from warehouse "
        "routing settings: storage | dispatch_staging | pack | input | other. "
        "dispatch_staging (Output) is staged for distributor delivery and must not "
        "be treated as free stock for another warehouse dispatch. free_qty used by "
        "dispatch is unchanged."
    )
    stock["get"]["responses"]["200"] = {
        "description": (
            "ok:true with items[{id, product, warehouse, location, stock_role, "
            "quantity, reserved_quantity, available_quantity}]"
        )
    }

issue = doc["paths"].get("/almohit/api/warehouse/issues/{picking_id}", {})
if issue.get("get"):
    issue["get"]["description"] = (
        "Detail includes lines, available_warehouses, dispatch_picking_id (primary), "
        "dispatch_picking_ids (all stock→output pickings), and result_pickings. "
        "current_warehouse / suggested_warehouse are informational only."
    )

path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("openapi patched", doc["info"]["version"])
