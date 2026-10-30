from odoo.addons.almohit_core.services.warehouse_common import allowed_location_ids, location_in_warehouses
from odoo.addons.almohit_core.services import warehouse_issues
import json
partner = env["res.partner"].browse(388)
asg = env["almohit.warehouse.assignment"].search([("partner_id","=",388)], limit=1)
print("allowed", sorted(allowed_location_ids(asg.warehouse_ids)))
p = env["stock.picking"].browse(22)
print("in_wh", location_in_warehouses(p.location_id, asg.warehouse_ids))
def body(res):
    raw = res.response[0]
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
print("get", body(warehouse_issues.get_issue(partner, 22)).get("ok"), body(warehouse_issues.get_issue(partner, 22)).get("error"))
print("retry", body(warehouse_issues.dispatch_issue(partner, 22, {"lines":[{"move_id":1,"quantity_done":1}]})).get("error"))