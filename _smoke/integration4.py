import json
from odoo.addons.almohit_core.services import warehouse_transfers, warehouse_issues
from odoo.addons.almohit_core.services.stock import order_allows_distribution

def body_of(res):
    raw = res.response[0]
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)

def onhand(env, product, location):
    return float(sum(env["stock.quant"].sudo().search([
        ("product_id", "=", product.id), ("location_id", "=", location.id)
    ]).mapped("quantity")))

partner = env["res.partner"].browse(388)
wh = env["stock.warehouse"].browse(1)
wh2 = env["stock.warehouse"].search([("code","=","WH2")], limit=1)
asg = env["almohit.warehouse.assignment"].search([("partner_id","=",388)], limit=1)
asg.write({"warehouse_ids":[(6,0,[wh.id, wh2.id])],"default_warehouse_id": wh.id,
           "perm_stock_read":True,"perm_receipt":True,"perm_issue":True,
           "perm_transfer_create":True,"perm_transfer_receive":True})

product = env["product.product"].search([("default_code","=","AJWA-1KG-001")], limit=1)
loc = wh.lot_stock_id
print("product", product.default_code, "onhand", onhand(env, product, loc))

b = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id, "dest_warehouse_id": wh2.id,
    "lines":[{"product_id": product.id, "quantity": 3, "location_id": loc.id}],
}))
print("create", b.get("ok"), b.get("error"), b.get("data",{}).get("id"), b.get("data",{}).get("state"))
tid = b["data"]["id"]; line_id = b["data"]["lines"][0]["id"]
qb = onhand(env, product, loc)
b = body_of(warehouse_transfers.dispatch_transfer(partner, tid, {}))
print("dispatch", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))
print("src_delta", onhand(env, product, loc) - qb)

b = body_of(warehouse_transfers.receive_transfer(partner, tid, {
    "lines":[{"line_id": line_id, "quantity": 2, "location_id": wh2.lot_stock_id.id, "reason":"missing"}],
}))
print("partial", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))
tr = env["almohit.warehouse.transfer"].browse(tid)
print("recv", float(tr.line_ids.qty_received), "disc", [(d.id, d.state, float(d.qty_difference)) for d in tr.discrepancy_ids])
print("dest_onhand", onhand(env, product, wh2.lot_stock_id))
for d in tr.discrepancy_ids.filtered(lambda x: x.state == "open"):
    print("resolve", body_of(warehouse_transfers.resolve_discrepancy(partner, d.id, {"note":"ok"})).get("ok"))
b = body_of(warehouse_transfers.close_transfer(partner, tid, {"note":"done"}))
print("close", b.get("ok"), b.get("data",{}).get("state"), b.get("error"))

# reject after dispatch
b = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id, "dest_warehouse_id": wh2.id,
    "lines":[{"product_id": product.id, "quantity": 1, "location_id": loc.id}],
}))
tid4 = b["data"]["id"]
print("disp4", body_of(warehouse_transfers.dispatch_transfer(partner, tid4, {})).get("ok"), body_of(warehouse_transfers.dispatch_transfer(partner, tid4, {})).get("error") if False else "")
# dispatch once properly
# already may have been dispatched above incorrectly double-called - fix:
tr4 = env["almohit.warehouse.transfer"].browse(tid4)
if tr4.state == "requested":
    print("disp4b", body_of(warehouse_transfers.dispatch_transfer(partner, tid4, {})).get("data",{}).get("state"))
elif tr4.state != "dispatched":
    print("disp4 state", tr4.state)
else:
    print("disp4 already", tr4.state)
b = body_of(warehouse_transfers.reject_transfer(partner, tid4, {"note":"late"}))
print("reject_after_disp", b.get("ok"), b.get("error"))

# issue
chosen=None
for p in env["stock.picking"].sudo().search([("picking_type_code","=","outgoing"),("state","=","assigned"),("almohit_dispatch_state","!=","dispatched")], limit=100):
    if p.sale_id and getattr(p.sale_id, "almohit_storefront_order", False) and not order_allows_distribution(p.sale_id):
        continue
    if not p.move_ids.filtered(lambda m: m.state not in ("done","cancel")):
        continue
    chosen=p
    break
print("chosen", chosen and chosen.id, chosen and chosen.name)
if chosen:
    move = chosen.move_ids.filtered(lambda m: m.state not in ("done","cancel"))[:1]
    prod = move.product_id
    out = wh.wh_output_stock_loc_id
    print("before", onhand(env, prod, wh.lot_stock_id), onhand(env, prod, out), "move", move.product_id.default_code, move.id)
    b = body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {"lines":[{"move_id": move.id, "quantity_done": 1.0, "location_id": wh.lot_stock_id.id}]}))
    print("issue", b.get("ok"), b.get("error"), b.get("data",{}).get("dispatch_state"), b.get("data",{}).get("dispatch_picking_id"))
    chosen.invalidate_recordset()
    print("after", onhand(env, prod, wh.lot_stock_id), onhand(env, prod, out), "cust", chosen.state, chosen.location_id.complete_name, chosen.almohit_dispatch_state)
    print("retry", body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {"lines":[{"move_id": move.id, "quantity_done": 1.0}]})).get("error"))

# purchase receipt receive if remaining
rec = env["stock.picking"].browse(74)
print("receipt74", rec.state, [(m.id, m.product_uom_qty, m.quantity, m.state) for m in rec.move_ids])

env.cr.commit()
print("DONE")