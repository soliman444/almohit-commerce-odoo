from odoo.addons.almohit_core.services.customer import serialize_auth_user, serialize_warehouse_access, get_warehouse_assignment
u = env["res.users"].browse(261)
# 1) normal path
data = serialize_auth_user(u)
print("NORMAL", data.get("role"), bool(data.get("warehouse_access",{}).get("warehouses")))
# 2) simulate hosted missing model
env_type = type(u.env)
orig_contains = env_type.__contains__
orig_getitem = env_type.__getitem__
def contains(self, name):
    if name == "almohit.warehouse.assignment":
        return False
    return orig_contains(self, name)
def getitem(self, name):
    if name == "almohit.warehouse.assignment":
        raise KeyError(name)
    return orig_getitem(self, name)
env_type.__contains__ = contains
env_type.__getitem__ = getitem
try:
    print("MISSING_ASSIGN", get_warehouse_assignment(u.partner_id))
    print("MISSING_ACCESS", serialize_warehouse_access(u.partner_id.sudo()))
    data2 = serialize_auth_user(u)
    print("MISSING_LOGIN_PAYLOAD", data2.get("role"), data2.get("warehouse_access"))
finally:
    env_type.__contains__ = orig_contains
    env_type.__getitem__ = orig_getitem
env.cr.rollback()
print("STAGING_OK")