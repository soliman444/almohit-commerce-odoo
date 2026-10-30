import json
from odoo.addons.almohit_core.services import warehouse_transfers, warehouse_issues, warehouse_inventory
from odoo.addons.almohit_core.services.warehouse_common import receipt_validation_mode, ICP_RECEIPT_MODE

def body_of(res):
    raw = res.response[0]
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw), getattr(res, "status", None)

partner = env["res.partner"].browse(388)
wh = env["stock.warehouse"].browse(1)
Company = env.company
Wh = env["stock.warehouse"].sudo()
wh2 = Wh.search([("code", "=", "WH2")], limit=1)
if not wh2:
    wh2 = Wh.create({"name": "Secondary WH", "code": "WH2", "company_id": Company.id})
asg = env["almohit.warehouse.assignment"].search([("partner_id", "=", 388)], limit=1)
asg.write({
    "warehouse_ids": [(6, 0, [wh.id, wh2.id])],
    "default_warehouse_id": wh.id,
    "perm_stock_read": True,
    "perm_receipt": True,
    "perm_issue": True,
    "perm_transfer_create": True,
    "perm_transfer_receive": True,
})
print("setup", wh.id, wh2.id, asg.warehouse_ids.ids)

# receipt modes
icp = env["ir.config_parameter"].sudo()
from odoo.addons.almohit_core.services.warehouse_receipts import receive_receipt
icp.set_param(ICP_RECEIPT_MODE, "buyer_only")
b, st = body_of(receive_receipt(partner, 74, {"lines": []}))
print("mode_buyer_only", b.get("error", {}).get("code"))
icp.set_param(ICP_RECEIPT_MODE, "warehouse_only")
# buyer gate via buyer service
from odoo.addons.almohit_core.services import buyer as buyer_svc
# find a buyer user linked to purchase
print("mode_wh_only_configured", receipt_validation_mode(env))
icp.set_param(ICP_RECEIPT_MODE, "both")

# inventory approve existing preview session 1
sess = env["almohit.inventory.session"].sudo().browse(1)
product = env["product.product"].search([("default_code", "=", "AJWA-1KG-001")], limit=1)
loc = wh.lot_stock_id
qty_before = sum(env["stock.quant"].sudo().search([("product_id","=",product.id),("location_id","=",loc.id)]).mapped("quantity"))
print("inv_state", sess.exists() and sess.state, "qty_before", qty_before)
if sess.exists() and sess.state == "preview":
    b, st = body_of(warehouse_inventory.approve_session(partner, sess.id, {}))
    print("approve", b.get("ok"), b.get("error"), b.get("data", {}).get("state") if b.get("ok") else None)
    qty_after = sum(env["stock.quant"].sudo().search([("product_id","=",product.id),("location_id","=",loc.id)]).mapped("quantity"))
    print("qty_after_approve", qty_after)
else:
    # create new session via import helper path: write counted small
    sess2 = env["almohit.inventory.session"].sudo().create({
        "session_type": "opening",
        "warehouse_id": wh.id,
        "location_id": loc.id,
        "state": "preview",
        "line_ids": [(0, 0, {
            "product_id": product.id,
            "product_uom_id": product.uom_id.id,
            "qty_on_hand": qty_before,
            "qty_counted": qty_before,  # no-op apply
            "row_number": 2,
        })],
    })
    b, st = body_of(warehouse_inventory.approve_session(partner, sess2.id, {}))
    print("approve_new", b.get("ok"), b.get("error"), b.get("data", {}).get("state") if b.get("ok") else None)

# transfer full cycle
qty_src0 = env["stock.quant"]._get_available_quantity(product, loc)
b, _ = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 3, "location_id": loc.id}],
}))
print("create", b.get("ok"), b.get("data", {}).get("id"), b.get("data", {}).get("state"), b.get("error"))
tid = b["data"]["id"]
line_id = b["data"]["lines"][0]["id"]
b, _ = body_of(warehouse_transfers.dispatch_transfer(partner, tid, {}))
print("dispatch", b.get("ok"), b.get("data", {}).get("state"), "qty_disp", b["data"]["lines"][0]["qty_dispatched"] if b.get("ok") else b.get("error"))
qty_src1 = env["stock.quant"]._get_available_quantity(product, loc)
print("src_after_dispatch", qty_src1, "delta", qty_src1 - qty_src0)

b, _ = body_of(warehouse_transfers.receive_transfer(partner, tid, {
    "lines": [{"line_id": line_id, "quantity": 2, "location_id": wh2.lot_stock_id.id, "reason": "missing"}],
}))
print("partial", b.get("ok"), b.get("data", {}).get("state") if b.get("ok") else b.get("error"))
tr = env["almohit.warehouse.transfer"].browse(tid)
print("lines", [(l.qty_dispatched, l.qty_received) for l in tr.line_ids], "disc", tr.discrepancy_ids.mapped(lambda d: (d.id, d.state, d.qty_difference)))
qty_dest = env["stock.quant"]._get_available_quantity(product, wh2.lot_stock_id)
print("dest_qty", qty_dest)

for d in tr.discrepancy_ids.filtered(lambda x: x.state == "open"):
    b, _ = body_of(warehouse_transfers.resolve_discrepancy(partner, d.id, {"note": "short accepted"}))
    print("resolve", d.id, b.get("ok"), b.get("error"))

b, _ = body_of(warehouse_transfers.close_transfer(partner, tid, {"note": "closed"}))
print("close", b.get("ok"), b.get("data", {}).get("state") if b.get("ok") else b.get("error"))

# cancel + reject
b, _ = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": loc.id}],
}))
tid2 = b["data"]["id"]
b, _ = body_of(warehouse_transfers.cancel_transfer(partner, tid2, {"note": "abort"}))
print("cancel", b.get("ok"), b.get("data", {}).get("state") if b.get("ok") else b.get("error"))

b, _ = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": loc.id}],
}))
tid3 = b["data"]["id"]
tr3 = env["almohit.warehouse.transfer"].browse(tid3)
if tr3.state == "draft":
    tr3.state = "requested"
b, _ = body_of(warehouse_transfers.reject_transfer(partner, tid3, {"note": "no space"}))
print("reject", b.get("ok"), b.get("data", {}).get("state") if b.get("ok") else b.get("error"))

# reject after dispatch -> TRANSFER_NEEDS_RECONCILE
b, _ = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": loc.id}],
}))
tid4 = b["data"]["id"]
warehouse_transfers.dispatch_transfer(partner, tid4, {})
b, _ = body_of(warehouse_transfers.reject_transfer(partner, tid4, {"note": "late"}))
print("reject_after_dispatch", b.get("ok"), b.get("error", {}).get("code"))

# issue dispatch: find candidate with remaining qty and payment ok
Pick = env["stock.picking"].sudo()
chosen = None
from odoo.addons.almohit_core.services.stock import order_allows_distribution
for p in Pick.search([("picking_type_code","=","outgoing"),("state","=","assigned"),("almohit_dispatch_state","!=","dispatched")], limit=80):
    if p.sale_id and p.sale_id.almohit_storefront_order and not order_allows_distribution(p.sale_id):
        continue
    moves = [m for m in p.move_ids if m.state not in ("done","cancel") and float(m.product_uom_qty or 0) > 0]
    if not moves:
        continue
    # skip if quantity already fully done
    if all(float(getattr(m, "quantity", 0) or 0) >= float(m.product_uom_qty or 0) for m in moves):
        # still can dispatch based on ordered qty in our code
        pass
    chosen = p
    break
print("chosen_issue", chosen and chosen.id, chosen and chosen.name)
if chosen:
    move = chosen.move_ids.filtered(lambda m: m.state not in ("done","cancel"))[:1]
    prod = move.product_id
    qb = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
    qo = env["stock.quant"]._get_available_quantity(prod, wh.wh_output_stock_loc_id)
    qty_do = min(1.0, float(move.product_uom_qty or 1.0))
    b, _ = body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {
        "lines": [{"move_id": move.id, "quantity_done": qty_do, "location_id": wh.lot_stock_id.id}],
    }))
    print("issue_dispatch", b.get("ok"), b.get("error"), b.get("data", {}).get("dispatch_state") if b.get("ok") else None)
    chosen.invalidate_recordset()
    print("cust_state", chosen.state, "disp", chosen.almohit_dispatch_state, "dpick", chosen.almohit_dispatch_picking_id.display_name, chosen.almohit_dispatch_picking_id.state)
    qa = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
    qoa = env["stock.quant"]._get_available_quantity(prod, wh.wh_output_stock_loc_id)
    print("issue_stock_delta stock", qa-qb, "output", qoa-qo)
    b2, _ = body_of(warehouse_issues.dispatch_issue(partner, chosen.id, {
        "lines": [{"move_id": move.id, "quantity_done": qty_do, "location_id": wh.lot_stock_id.id}],
    }))
    print("issue_retry", b2.get("ok"), b2.get("error", {}).get("code"))
    qr = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
    print("retry_stock_delta", qr-qa)

# unassigned denial
asg.write({"warehouse_ids": [(6, 0, [])]})
try:
    asg.write({"warehouse_ids": [(6, 0, [])]})
except Exception as e:
    print("empty_assign_constraint", type(e).__name__)
# restore then clear via active=False style: remove WH2 only and test cross
asg.write({"warehouse_ids": [(6, 0, [wh.id])], "default_warehouse_id": wh.id})
b, _ = body_of(warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh2.id,
    "dest_warehouse_id": wh.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": wh2.lot_stock_id.id}],
}))
print("unassigned_src", b.get("ok"), b.get("error", {}).get("code"))

# restore multi
asg.write({"warehouse_ids": [(6, 0, [wh.id, wh2.id])], "default_warehouse_id": wh.id})
env.cr.commit()
print("DONE")