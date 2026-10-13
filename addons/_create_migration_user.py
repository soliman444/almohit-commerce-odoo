Users = env["res.users"].with_context(no_reset_password=True)
login = "catalog.migration@almohit.local"
password = "AlmohitMigrate-Catalog-17"
user = Users.search([("login", "=", login)], limit=1)
system = env.ref("base.group_system")
stock = env.ref("stock.group_stock_manager", raise_if_not_found=False)
sale = env.ref("sales_team.group_sale_manager", raise_if_not_found=False)
groups = system
if stock:
    groups |= stock
if sale:
    groups |= sale
if user:
    user.write({"password": password, "groups_id": [(6, 0, groups.ids)]})
    print("updated", user.id)
else:
    user = Users.create({
        "name": "Almohit Catalog Migration",
        "login": login,
        "password": password,
        "groups_id": [(6, 0, groups.ids)],
    })
    print("created", user.id)
env["ir.config_parameter"].sudo().set_param("almohit.migration_token", "AlmohitMigrate-Catalog-17")
env.cr.commit()
print("ready", user.login, user.has_group("base.group_system"))
