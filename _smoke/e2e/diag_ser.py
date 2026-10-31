Partner = env["res.partner"]
print("has_lat", "partner_latitude" in Partner._fields)
print("has_long", "partner_longitude" in Partner._fields)
print("has_assignment", "almohit.warehouse.assignment" in env)
print("role_selection", Partner._fields["almohit_app_role"].selection)
# Simulate serialize without assignment model
try:
    del env.registry.models  # don't do this
except Exception as e:
    print(e)
# Call serialize on WH partner
from odoo.addons.almohit_core.services.customer import serialize_auth_user
u = env["res.users"].browse(261)
print("serialize_ok", serialize_auth_user(u).get("role"), "wh" in serialize_auth_user(u))
print("keys", list(serialize_auth_user(u).keys()))
# Simulate missing model
from odoo.addons.almohit_core.services import customer as c
old = c.get_warehouse_assignment
def boom(partner):
    return partner.env["almohit.warehouse.assignment.MISSING"]
try:
    partner = u.partner_id
    partner.env["does.not.exist"]
except Exception as e:
    print("missing_model_type", type(e).__name__, str(e)[:120])
env.cr.rollback()