import json
from odoo.addons.almohit_core.services import warehouse_transfers, warehouse_issues
from odoo.addons.almohit_core.services.stock import order_allows_distribution

def body_of(res):
    raw = res.response[0]
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)

partner = env["res.partner"].browse(388)
wh = env["stock.warehouse"].browse(1)
wh2 = env["stock.warehouse"].search([("code","=","WH2")], limit=1)
asg = env["almohit.warehouse.assignment"].search([("partner_id","=",388)], limit=1)
asg.write({"warehouse_ids":[(6,0,[wh.id, wh2.id])],"default_warehouse_id": wh.id,
           "perm_stock_read":True,"perm_receipt":True,"perm_issue":True,
           "perm_transfer_create":True,"perm_transfer_receive":True})

# restore AJWA stock for sanity
ajwa = env["product.product"].search([("default_code","=","AJWA-1KG-001")], limit=1)
Quant = env["stock.quant"].with_user(1).sudo()
q = Quant.search([("product_id","=",ajwa.id),("location_id","=",wh.lot_stock_id.id),("lot_id","=",False),("package_id","=",False),("owner_id","=",False)], limit=1)
if q:
    q.inventory_quantity = 90.0
    q.action_apply_inventory()
print("ajwa_restored", sum(Quant.search([("product_id","=",ajwa.id),("location_id","=",wh.lot_stock_id.id)]).mapped("quantity")))

product = env["product.product"].search([("default_code","=","BEV-WTR-010")], limit=1)
loc = wh.lot_stock_id
print("product", product.default_code, "avail", env["stock.quant"]._get_available_quantity(product, loc))

# full transfer cycle qty 3
b = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id, "dest_warehouse_id": wh2.id,
    "lines":[{"product_id": product.id, "quantity": 3, "location_id": loc.id}],
}))
tid = b["data"]["id"]; line_id = b["data"]["lines"][0]["id"]
print("create", tid, b["data"]["state"])
qb = env["stock.quant"]._get_available_quantity(product, loc)
b = body_of(warehouse_transfers.dispatch_transfer(partner, tid, {}))
print("dispatch", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))
qa = env["stock.quant"]._get_available_quantity(product, loc)
print("src_delta", qa-qb)
transit = env["stock.location"].search([("usage","=","transit"),("company_id","in",[False, env.company.id])], limit=5)
print("transit_qtys", [(t.complete_name, env["stock.quant"]._get_available_quantity(product, t)) for t in transit])

b = body_of(warehouse_transfers.receive_transfer(partner, tid, {
    "lines":[{"line_id": line_id, "quantity": 2, "location_id": wh2.lot_stock_id.id, "reason":"missing"}],
}))
print("partial", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))
tr = env["almohit.warehouse.transfer"].browse(tid)
print("recv_line", tr.line_ids.qty_received, "disc", [(d.id,d.state,d.qty_difference) for d in tr.discrepancy_ids])
print("dest_avail", env["stock.quant"]._get_available_quantity(product, wh2.lot_stock_id))
for d in tr.discrepancy_ids.filtered(lambda x: x.state=="open"):
    print("resolve", body_of(warehouse_transfers.resolve_discrepancy(partner, d.id, {"note":"ok"})).get("ok"))
print("close", body_of(warehouse_transfers.close_transfer(partner, tid, {})).get("data",{}).get("state"), body_of(warehouse_transfers.close_transfer(partner, tid, {})).get("error") if False else "")
# close once
b = body_of(warehouse_transfers.close_transfer(partner, tid, {"note":"done"}))
print("close2", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))

# reject after successful dispatch
b = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id, "dest_warehouse_id": wh2.id,
    "lines":[{"product_id": product.id, "quantity": 1, "location_id": loc.id}],
}))
tid4 = b["data"]["id"]
print("disp4", body_of(warehouse_transfers.dispatch_transfer(partner, tid4, {})).get("data",{}).get("state"))
b = body_of(warehouse_transfers.reject_transfer(partner, tid4, {"note":"late"}))
print("reject_after_disp", b.get("ok"), b.get("error"))

# inspect previous issue 22
p = env["stock.picking"].browse(22)
print("issue22", p.state, p.location_id.complete_name, p.almohit_dispatch_state, p.almohit_dispatch_picking_id.name, p.almohit_dispatch_picking_id.state)
if p.almohit_dispatch_picking_id:
    dp = p.almohit_dispatch_picking_id
    print("dpick_moves", [(m.product_id.default_code, m.product_uom_qty, m.location_id.complete_name, m.location_dest_id.complete_name, m.state) for m in dp.move_ids])

# new issue dispatch with stock evidence
chosen=None
for p in env["stock.picking"].sudo().search([("picking_type_code","=","outgoing"),("state","=","assigned"),("almohit_dispatch_state","!=","dispatched")], limit=100):
    if p.sale_id and getattr(p.sale_id, "almohit_storefront_order", False) and not order_allows_distribution(p.sale_id):
        continue
    moves = p.move_ids.filtered(lambda m: m.state not in ("done","cancel"))
    if not moves:
        continue
    chosen=p
    break
print("chosen", chosen.id if chosen else None, chosen.name if chosen else None)
if chosen:
    move = chosen.move_ids.filtered(lambda m: m.state not in ("done","cancel"))[:1]
    prod = move.product_id
    out = wh.wh_output_stock_loc_id
    def onhand(location):
        return sum(env["stock.quant"].sudo().search([("product_id","=",prod.id),("location_id","=",location.id)]).mapped("quantity"))
    print("before stock", onhand(wh.lot_stock_id), "out", onhand(out), "avail", env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id))
    b = body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {"lines":[{"move_id": move.id, "quantity_done": 1.0, "location_id": wh.lot_stock_id.id}]}))
    print("dispatch", b.get("ok"), b.get("error"), b.get("data",{}).get("dispatch_state"))
    chosen.invalidate_recordset()
    print("after stock", onhand(wh.lot_stock_id), "out", onhand(out), "cust_loc", chosen.location_id.complete_name, "cust_state", chosen.state)
    print("retry", body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {"lines":[{"move_id": move.id, "quantity_done": 1.0, "location_id": wh.lot_stock_id.id}]})).get("error"))

env.cr.commit()
print("DONE")