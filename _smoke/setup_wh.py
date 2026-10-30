partner = env["res.partner"].browse(388)
print("partner", partner.name, getattr(partner, "almohit_app_role", None), partner.almohit_trade_status)
apps = env["almohit.trade.application"].search([("partner_id","=",388)], order="id desc", limit=1)
print("app", apps.id, apps.status if apps else None)
if apps and apps.status == "pending":
    apps.action_approve()
    print("approved", apps.status, partner.almohit_trade_status)
wh = env["stock.warehouse"].browse(1)
Assignment = env["almohit.warehouse.assignment"]
asg = Assignment.search([("partner_id","=",388)], limit=1)
vals = {
    "partner_id": 388,
    "warehouse_ids": [(6, 0, [wh.id])],
    "default_warehouse_id": wh.id,
    "perm_stock_read": True,
    "perm_receipt": True,
    "perm_issue": True,
    "perm_transfer_create": True,
    "perm_transfer_receive": True,
    "active": True,
}
if asg:
    asg.write(vals)
else:
    asg = Assignment.create(vals)
print("assignment", asg.id, asg.warehouse_ids.ids, asg.permission_codes())
user = env["res.users"].browse(261)
print("groups", user.groups_id.mapped("name"))
icp = env["ir.config_parameter"].sudo()
print("receipt_mode", icp.get_param("almohit.receipt_validation_mode", "both"))
print("lot_stock", wh.lot_stock_id.id, wh.lot_stock_id.complete_name)
print("out_loc", wh.wh_output_stock_loc_id.id if wh.wh_output_stock_loc_id else None)
# products with qty
quants = env["stock.quant"].search([("location_id","=",wh.lot_stock_id.id),("quantity",">",0)], limit=5)
print("stock_sample", [(q.product_id.id, q.product_id.default_code, q.quantity) for q in quants])
env.cr.commit()
print("OK")