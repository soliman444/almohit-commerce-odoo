wh = env["stock.warehouse"].browse(1)
print("view", wh.view_location_id.id, wh.view_location_id.complete_name)
print("stock", wh.lot_stock_id.id, wh.lot_stock_id.complete_name, "parent", wh.lot_stock_id.location_id.id)
print("out", wh.wh_output_stock_loc_id.id, wh.wh_output_stock_loc_id.complete_name, "parent", wh.wh_output_stock_loc_id.location_id.id, "usage", wh.wh_output_stock_loc_id.usage)
print("pack", wh.wh_pack_stock_loc_id.id if wh.wh_pack_stock_loc_id else None, wh.wh_pack_stock_loc_id.complete_name if wh.wh_pack_stock_loc_id else None)
Loc = env["stock.location"].sudo()
children = Loc.search([("id","child_of", wh.view_location_id.id)])
print("children", [(c.id, c.complete_name, c.usage) for c in children])
out = wh.wh_output_stock_loc_id
print("out parent chain")
cur = out
while cur:
    print(" ", cur.id, cur.complete_name, cur.usage)
    cur = cur.location_id