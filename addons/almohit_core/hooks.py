def post_init_hook(env):
    """Reuse the native company warehouse; create one only if stock left none."""
    Warehouse = env["stock.warehouse"]
    for company in env["res.company"].search([]):
        existing = Warehouse.search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        if existing:
            continue
        Warehouse.create({
            "name": company.name or "Warehouse",
            "code": "WH",
            "company_id": company.id,
        })
