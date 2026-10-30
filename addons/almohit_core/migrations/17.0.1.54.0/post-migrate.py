def migrate(cr, version):
    cr.execute(
        """
        UPDATE stock_picking sp
           SET almohit_storefront_delivery = TRUE
          FROM sale_order so
         WHERE so.id = sp.sale_id
           AND so.almohit_storefront_order IS TRUE
        """
    )
    # Origin-based fallback for pickings without sale_id link
    cr.execute(
        """
        UPDATE stock_picking sp
           SET almohit_storefront_delivery = TRUE
          FROM sale_order so
         WHERE sp.sale_id IS NULL
           AND sp.origin IS NOT NULL
           AND so.name = sp.origin
           AND so.company_id = sp.company_id
           AND so.almohit_storefront_order IS TRUE
        """
    )
