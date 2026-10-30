from odoo import fields
PWD = "E2EPass123!"
accounts = [
    ("e2e.release.buyer@example.test", "buyer", "E2E Release Buyer"),
    ("e2e.release.merchant@example.test", "merchant", "E2E Release Merchant"),
    ("e2e.release.marketer@example.test", "marketer", "E2E Release Marketer"),
    ("e2e.release.dist@example.test", "distributor", "E2E Release Dist"),
    ("wh.tester.final@example.com", "warehouse_manager", "WH Tester"),
]
Users = env["res.users"].sudo().with_context(no_reset_password=True)
Partner = env["res.partner"].sudo()
group_map = {
    "buyer": "almohit_core.group_almohit_buyer",
    "merchant": "almohit_core.group_almohit_merchant",
    "marketer": "almohit_core.group_almohit_marketer",
    "distributor": "almohit_core.group_almohit_distributor",
    "warehouse_manager": "almohit_core.group_almohit_warehouse_manager",
}
portal = env.ref("base.group_portal")
created = {}
for login, role, name in accounts:
    user = Users.search([("login", "=", login)], limit=1)
    group = env.ref(group_map[role])
    if not user:
        partner = Partner.create({
            "name": name,
            "email": login,
            "almohit_app_role": role,
            "almohit_trade_status": "approved",
            "phone": "+966500111222",
        })
        user = Users.create({
            "name": name,
            "login": login,
            "partner_id": partner.id,
            "groups_id": [(6, 0, [portal.id, group.id])],
        })
    else:
        partner = user.partner_id
        partner.write({
            "almohit_app_role": role,
            "almohit_trade_status": "approved",
            "phone": partner.phone or "+966500111222",
        })
        user.write({"groups_id": [(4, portal.id), (4, group.id)]})
    user.password = PWD
    created[role] = {"user_id": user.id, "partner_id": partner.id, "login": login}
    print("user", role, login, user.id, partner.id)

# WH assignment
wh = env["stock.warehouse"].browse(1)
wh2 = env["stock.warehouse"].search([("code","=","WH2")], limit=1)
if not wh2:
    wh2 = env["stock.warehouse"].create({"name":"Secondary WH","code":"WH2","company_id": env.company.id})
# secondary internal location under WH/Stock for split receive
Loc = env["stock.location"].sudo()
split_loc = Loc.search([("name","=","E2E-Split-A"), ("location_id","=", wh.lot_stock_id.id)], limit=1)
if not split_loc:
    split_loc = Loc.create({
        "name": "E2E-Split-A",
        "usage": "internal",
        "location_id": wh.lot_stock_id.id,
        "company_id": env.company.id,
    })
print("split_loc", split_loc.id, split_loc.complete_name)

wh_partner = Partner.browse(created["warehouse_manager"]["partner_id"])
Asg = env["almohit.warehouse.assignment"].sudo()
asg = Asg.search([("partner_id","=", wh_partner.id)], limit=1)
vals = {
    "partner_id": wh_partner.id,
    "warehouse_ids": [(6,0,[wh.id, wh2.id])],
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
    asg = Asg.create(vals)
print("assignment", asg.id, asg.warehouse_ids.ids)

env["ir.config_parameter"].sudo().set_param("almohit.receipt_validation_mode", "both")
print("receipt_mode both")
print("lot_stock", wh.lot_stock_id.id, "output", wh.wh_output_stock_loc_id.id)
print("DONE_SETUP")
env.cr.commit()