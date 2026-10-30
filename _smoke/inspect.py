tr = env["almohit.warehouse.transfer"].browse(1)
print("t1", tr.state, [(l.id, l.qty_requested, l.qty_dispatched, l.qty_received) for l in tr.line_ids])
print("disc", [(d.id, d.state, d.qty_difference) for d in tr.discrepancy_ids])
for t in env["almohit.warehouse.transfer"].search([]):
    print("T", t.id, t.state, t.name)
sess = env["almohit.inventory.session"].browse(1)
print("inv", sess.state, sess.name)
# find open outgoing with remaining and payment ok
Pick = env["stock.picking"].sudo()
cands = Pick.search([("picking_type_code","=","outgoing"),("state","in",("assigned","confirmed")),("almohit_dispatch_state","!=","dispatched")], limit=30)
for p in cands:
    sale = p.sale_id
    rem = sum(max(m.product_uom_qty - m.quantity, 0) for m in p.move_ids if m.state not in ("done","cancel"))
    # Odoo 17 quantity field
    moves = [(m.id, m.product_id.default_code, m.product_uom_qty, getattr(m, "quantity", None), m.state) for m in p.move_ids]
    pay = False
    if sale:
        from odoo.addons.almohit_core.services.stock import order_allows_distribution
        try:
            pay = order_allows_distribution(sale)
        except Exception as e:
            pay = str(e)
    if rem > 0 or True:
        print("ISSUE", p.id, p.name, p.state, "rem_est", rem, "pay", pay, "moves", moves[:2])