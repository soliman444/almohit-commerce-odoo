from odoo.addons.almohit_core.services.customer import serialize_auth_user
u = env["res.users"].browse(261)
# Simulate missing partner_latitude field access
partner = u.partner_id
class Proxy:
    def __init__(self, real):
        self._real = real
    def __getattr__(self, name):
        if name in ("partner_latitude", "partner_longitude"):
            raise AttributeError(name)
        return getattr(self._real, name)
# direct attribute error path in serialize
try:
    _ = partner.does_not_exist_xyz
except AttributeError as e:
    print("attr_err_ok", type(e).__name__)
# What Odoo does for missing field via __getitem__ on record
try:
    partner["nope"]
except Exception as e:
    print("getitem", type(e).__name__, str(e)[:80])
print("lat_field", "partner_latitude" in partner._fields)
# If we remove field temporarily from _fields dict simulation
print("DONE")
env.cr.rollback()