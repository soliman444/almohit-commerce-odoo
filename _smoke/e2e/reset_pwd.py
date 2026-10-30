Users = env["res.users"].sudo().with_context(no_reset_password=True)
portal = env.ref("base.group_portal")
wh_group = env.ref("almohit_core.group_almohit_warehouse_manager")

def ensure_wh_user(login, name, trade_status, with_assignment=False):
    u = Users.search([("login", "=", login)], limit=1)
    if not u:
        p = env["res.partner"].sudo().create({
            "name": name,
            "email": login,
            "almohit_app_role": "warehouse_manager",
            "almohit_trade_status": trade_status,
            "phone": "+966500009999",
        })
        u = Users.create({
            "name": name,
            "login": login,
            "partner_id": p.id,
            "groups_id": [(6, 0, [portal.id, wh_group.id])],
        })
    else:
        u.partner_id.write({
            "almohit_app_role": "warehouse_manager",
            "almohit_trade_status": trade_status,
        })
        u.write({"groups_id": [(4, portal.id), (4, wh_group.id)]})
    u.password = "E2EPass123!"
    if not with_assignment:
        env["almohit.warehouse.assignment"].sudo().search(
            [("partner_id", "=", u.partner_id.id)]
        ).unlink()
    print("ensure", login, u.id, trade_status)
    return u

ensure_wh_user("e2e.wh.unapproved@example.test", "WH Unapproved", "pending", False)
ensure_wh_user("e2e.wh.unassigned@example.test", "WH Unassigned", "approved", False)

for login in [
    "e2e.release.buyer@example.test",
    "e2e.release.merchant@example.test",
    "e2e.release.marketer@example.test",
    "e2e.release.dist@example.test",
    "wh.tester.final@example.com",
]:
    u = Users.search([("login", "=", login)], limit=1)
    if u:
        u.password = "E2EPass123!"
        print("pwd", login, u.id)
    else:
        print("missing", login)
env["ir.config_parameter"].sudo().set_param("almohit.receipt_validation_mode", "both")
# ensure WH assignment still present
asg = env["almohit.warehouse.assignment"].sudo().search([("partner_id", "=", 388)], limit=1)
if asg:
    asg.write({
        "warehouse_ids": [(6, 0, [1, 2])],
        "default_warehouse_id": 1,
        "perm_stock_read": True,
        "perm_receipt": True,
        "perm_issue": True,
        "perm_transfer_create": True,
        "perm_transfer_receive": True,
        "active": True,
    })
    print("asg", asg.id, asg.warehouse_ids.ids)
env.cr.commit()
print("PWD_OK")
