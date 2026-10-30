from odoo.addons.almohit_core.services.warehouse_common import location_in_warehouses, allowed_location_ids, warehouse_for_location
partner = env["res.partner"].browse(388)
asg = env["almohit.warehouse.assignment"].search([("partner_id","=",388)], limit=1)
p = env["stock.picking"].browse(22)
print("loc", p.location_id.id, p.location_id.complete_name, p.almohit_dispatch_state)
print("in_wh", location_in_warehouses(p.location_id, asg.warehouse_ids))
print("wh_for", warehouse_for_location(p.location_id, asg.warehouse_ids).id)
ids = allowed_location_ids(asg.warehouse_ids)
print("out_in_allowed", p.location_id.id in ids, "sample", sorted(ids)[:15])
# storefront flag
print("storefront_delivery", p.almohit_storefront_delivery)
# retry dispatch code path
from odoo.addons.almohit_core.services import warehouse_issues
import json
res = warehouse_issues.dispatch_issue(partner, 22, {"lines":[{"move_id": p.move_ids[:1].id, "quantity_done": 1.0}]})
raw = res.response[0].decode() if isinstance(res.response[0], bytes) else res.response[0]
print("retry", json.loads(raw))
res2 = warehouse_issues.get_issue(partner, 22)
raw2 = res2.response[0].decode() if isinstance(res2.response[0], bytes) else res2.response[0]
print("get", json.loads(raw2).get("ok"), json.loads(raw2).get("error"))