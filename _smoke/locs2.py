Loc = env["stock.location"].sudo()
wh = env["stock.warehouse"].browse(1)
print("search child_of", Loc.search([("id","child_of", wh.view_location_id.id)]).ids)
print("search child_of active_test False", Loc.with_context(active_test=False).search([("id","child_of", wh.view_location_id.id)]).ids)
print("out active", Loc.browse(11).active, "company", Loc.browse(11).company_id.id)
# raw SQL via env
env.cr.execute("SELECT id FROM stock_location WHERE parent_path LIKE %s", [wh.view_location_id.parent_path + "%"])
print("sql", [r[0] for r in env.cr.fetchall()])
# company rule?
print("user company", env.company.id, env.user.company_ids.ids)
print("out browse exists", Loc.browse(11).exists())
print("search id 11", Loc.search([("id","=",11)]).ids)