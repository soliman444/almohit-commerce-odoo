from odoo.addons.almohit_core.services.customer import serialize_auth_user, serialize_warehouse_access, partner_app_role
u = env["res.users"].browse(261)
partner = u.partner_id.sudo()
print("role", partner_app_role(partner))
print("before", serialize_auth_user(u).get("warehouse_access"))
# Monkeypatch env getitem path used by get_warehouse_assignment
orig = type(env).__getitem__
def bad_getitem(self, name):
    if name == "almohit.warehouse.assignment":
        raise KeyError(name)
    return orig(self, name)
type(env).__getitem__ = bad_getitem
try:
    serialize_warehouse_access(partner)
    print("UNEXPECTED_OK")
except Exception as e:
    print("FAIL_TYPE", type(e).__name__)
    print("FAIL_MSG", str(e)[:200])
finally:
    type(env).__getitem__ = orig
# Also: role warehouse but field selection without warehouse_manager on older DB
print("AUTH_PHASE", "authenticate succeeds then serialize fails => HTML 500 if uncaught")
env.cr.rollback()