from odoo import SUPERUSER_ID, api

VALID_ROLES = ("buyer", "merchant", "marketer", "distributor")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Partner = env["res.partner"].with_context(active_test=False)
    Users = env["res.users"].sudo().with_context(active_test=False)
    role_groups = Partner._almohit_role_groups()
    all_group_ids = {group.id for group in role_groups.values()}
    partners = Partner.browse()
    for user in Users.search([("partner_id", "!=", False)]):
        role = user.partner_id.almohit_app_role
        if role not in VALID_ROLES:
            continue
        target = role_groups.get(role)
        if not target:
            continue
        current = set(user.groups_id.ids) & all_group_ids
        if current != {target.id}:
            partners |= user.partner_id
    if partners:
        partners._sync_almohit_role_groups()
