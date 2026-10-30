from odoo.addons.almohit_core.services import warehouse_transfers, warehouse_issues, warehouse_receipts, warehouse_inventory
from odoo.addons.almohit_core.services.warehouse_common import receipt_validation_mode, ICP_RECEIPT_MODE
from odoo.addons.almohit_core.services import buyer as buyer_svc
import json

partner = env["res.partner"].browse(388)
wh = env["stock.warehouse"].browse(1)
Company = env.company

# --- create second warehouse ---
Wh = env["stock.warehouse"].sudo()
wh2 = Wh.search([("code", "=", "WH2")], limit=1)
if not wh2:
    wh2 = Wh.create({"name": "Secondary WH", "code": "WH2", "company_id": Company.id})
print("wh2", wh2.id, wh2.lot_stock_id.id)

asg = env["almohit.warehouse.assignment"].search([("partner_id", "=", 388)], limit=1)
asg.write({"warehouse_ids": [(6, 0, [wh.id, wh2.id])], "default_warehouse_id": wh.id})
print("assigned", asg.warehouse_ids.ids)

# --- receipt mode enforcement ---
icp = env["ir.config_parameter"].sudo()
picking = env["stock.picking"].browse(74)
for mode, expect_buyer, expect_wh in [
    ("warehouse_only", "RECEIPT_HANDED_TO_WAREHOUSE", None),
    ("buyer_only", None, "RECEIPT_BUYER_ONLY"),
    ("both", None, None),
]:
    icp.set_param(ICP_RECEIPT_MODE, mode)
    assert receipt_validation_mode(env) == mode
    # buyer gate
    buyer_user = env["res.users"].search([("login", "ilike", "buyer")], limit=1)
    # call warehouse receive gate lightly via service entry
    from odoo.addons.almohit_core.services.warehouse_receipts import receive_receipt
    # skip actual receive; just check mode error early with empty lines on done? use invalid payload after mode check
    # Directly inspect mode branches by calling with empty lines when picking open
    res = receive_receipt(partner, 74, {"lines": []})
    body = json.loads(res.data.decode() if hasattr(res, "data") else res.response[0]) if hasattr(res, "data") or hasattr(res, "response") else None
    # Response object from api.error/ok
    status = getattr(res, "status", None) or getattr(res, "status_code", None)
    raw = res
    if hasattr(res, "response"):
        raw = res.response[0]
        if isinstance(raw, bytes):
            raw = raw.decode()
        body = json.loads(raw)
    elif isinstance(res, dict):
        body = res
    print("mode", mode, "wh_receive", body.get("ok"), body.get("error", {}).get("code") if not body.get("ok") else "ok-or-validation")

icp.set_param(ICP_RECEIPT_MODE, "both")

# --- permission denial ---
asg.write({"perm_issue": False})
env.cr.commit()
res = warehouse_issues.dispatch_issue(partner, 65, {"lines": []})
raw = res.response[0].decode() if isinstance(res.response[0], bytes) else res.response[0]
body = json.loads(raw)
print("perm_issue_denied", body.get("error", {}).get("code"), res.status)
asg.write({"perm_issue": True})

# --- transfer create/dispatch/partial receive/discrepancy/resolve/close ---
product = env["product.product"].search([("default_code", "=", "AJWA-1KG-001")], limit=1)
src_loc = wh.lot_stock_id
qty_before = env["stock.quant"]._get_available_quantity(product, src_loc)
print("qty_before_src", qty_before)

create_res = warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 3, "location_id": src_loc.id}],
})
raw = create_res.response[0].decode() if isinstance(create_res.response[0], bytes) else create_res.response[0]
body = json.loads(raw)
print("create_transfer", body.get("ok"), body.get("error"), body.get("data", {}).get("id") if body.get("ok") else None)
if not body.get("ok"):
    env.cr.commit()
    raise SystemExit(0)
tid = body["data"]["id"]

disp = warehouse_transfers.dispatch_transfer(partner, tid, {})
raw = disp.response[0].decode() if isinstance(disp.response[0], bytes) else disp.response[0]
body = json.loads(raw)
print("dispatch", body.get("ok"), body.get("data", {}).get("state") if body.get("ok") else body.get("error"))
qty_after_disp = env["stock.quant"]._get_available_quantity(product, src_loc)
print("qty_after_dispatch_src", qty_after_disp, "delta", qty_after_disp - qty_before)

# partial receive 2 of 3
recv = warehouse_transfers.receive_transfer(partner, tid, {
    "lines": [{"line_id": body["data"]["lines"][0]["id"] if body.get("data") and body["data"].get("lines") else None, "quantity": 2, "location_id": wh2.lot_stock_id.id, "reason": "missing"}],
})
# re-fetch transfer lines
tr = env["almohit.warehouse.transfer"].browse(tid)
line = tr.line_ids[:1]
recv = warehouse_transfers.receive_transfer(partner, tid, {
    "lines": [{"line_id": line.id, "quantity": 2, "location_id": wh2.lot_stock_id.id, "reason": "missing"}],
})
raw = recv.response[0].decode() if isinstance(recv.response[0], bytes) else recv.response[0]
body = json.loads(raw)
print("partial_receive", body.get("ok"), body.get("data", {}).get("state") if body.get("ok") else body.get("error"))
tr.invalidate_recordset()
print("transfer_state", tr.state, "disc", tr.discrepancy_ids.mapped(lambda d: (d.id, d.state, d.quantity)))

# resolve discrepancies
for d in tr.discrepancy_ids.filtered(lambda x: x.state == "open"):
    r = warehouse_transfers.resolve_discrepancy(partner, d.id, {"note": "accepted short"})
    raw = r.response[0].decode() if isinstance(r.response[0], bytes) else r.response[0]
    print("resolve", d.id, json.loads(raw).get("ok"), json.loads(raw).get("error"))

close = warehouse_transfers.close_transfer(partner, tid, {"note": "done"})
raw = close.response[0].decode() if isinstance(close.response[0], bytes) else close.response[0]
print("close", json.loads(raw))

# cancel draft transfer
create2 = warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": src_loc.id}],
})
raw = create2.response[0].decode() if isinstance(create2.response[0], bytes) else create2.response[0]
body = json.loads(raw)
tid2 = body["data"]["id"]
cancel = warehouse_transfers.cancel_transfer(partner, tid2, {"note": "abort"})
raw = cancel.response[0].decode() if isinstance(cancel.response[0], bytes) else cancel.response[0]
print("cancel", json.loads(raw).get("ok"), json.loads(raw).get("data", {}).get("state"))

# reject requested
create3 = warehouse_transfers.create_transfer(partner, {
    "source_warehouse_id": wh.id,
    "dest_warehouse_id": wh2.id,
    "lines": [{"product_id": product.id, "quantity": 1, "location_id": src_loc.id}],
})
raw = create3.response[0].decode() if isinstance(create3.response[0], bytes) else create3.response[0]
tid3 = json.loads(raw)["data"]["id"]
# set requested if create leaves draft
tr3 = env["almohit.warehouse.transfer"].browse(tid3)
if tr3.state == "draft":
    tr3.write({"state": "requested"})
rej = warehouse_transfers.reject_transfer(partner, tid3, {"note": "no room"})
raw = rej.response[0].decode() if isinstance(rej.response[0], bytes) else rej.response[0]
print("reject", json.loads(raw).get("ok"), json.loads(raw).get("data", {}).get("state"), json.loads(raw).get("error"))

# --- issue dispatch stock integrity (payment_ok issue 65) ---
issue = env["stock.picking"].browse(65)
print("issue", issue.name, issue.state, issue.almohit_dispatch_state, "moves", [(m.id, m.product_id.default_code, m.product_uom_qty, m.quantity) for m in issue.move_ids])
move = issue.move_ids[:1]
prod = move.product_id
q_before = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
q_out_before = env["stock.quant"]._get_available_quantity(prod, wh.wh_output_stock_loc_id)
disp = warehouse_issues.dispatch_issue(partner, 65, {
    "lines": [{"move_id": move.id, "quantity_done": 1, "location_id": wh.lot_stock_id.id}],
})
raw = disp.response[0].decode() if isinstance(disp.response[0], bytes) else disp.response[0]
body = json.loads(raw)
print("dispatch_issue", body.get("ok"), body.get("error"), body.get("data", {}).get("dispatch_state") if body.get("ok") else None)
issue.invalidate_recordset()
print("after_dispatch customer_state", issue.state, "dispatch_state", issue.almohit_dispatch_state, "dispatch_picking", issue.almohit_dispatch_picking_id.name, issue.almohit_dispatch_picking_id.state)
q_after = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
q_out_after = env["stock.quant"]._get_available_quantity(prod, wh.wh_output_stock_loc_id)
print("stock_delta", "stock", q_after - q_before, "output", q_out_after - q_out_before)
# retry should not duplicate
disp2 = warehouse_issues.dispatch_issue(partner, 65, {
    "lines": [{"move_id": move.id, "quantity_done": 1, "location_id": wh.lot_stock_id.id}],
})
raw = disp2.response[0].decode() if isinstance(disp2.response[0], bytes) else disp2.response[0]
print("dispatch_retry", json.loads(raw).get("ok"), json.loads(raw).get("error"))
q_retry = env["stock.quant"]._get_available_quantity(prod, wh.lot_stock_id)
print("stock_after_retry_delta_from_after", q_retry - q_after)

env.cr.commit()
print("DONE")