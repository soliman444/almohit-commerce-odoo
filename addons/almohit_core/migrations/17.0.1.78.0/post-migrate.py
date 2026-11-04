def migrate(cr, version):
    """Drop obsolete ACLs replaced by group_almohit_operations_admin exclusivity."""
    obsolete_xmlids = (
        "access_almohit_notification_log_user",
        "access_almohit_notification_template_user",
        "access_almohit_notification_template_system",
        "access_almohit_notification_preview_user",
        "access_almohit_whatsapp_session_log_user",
        "access_almohit_warehouse_assignment_user",
        "access_almohit_warehouse_assignment_stock_manager",
    )
    cr.execute(
        """
        DELETE FROM ir_model_access
        WHERE id IN (
            SELECT res_id
            FROM ir_model_data
            WHERE module = 'almohit_core'
              AND model = 'ir.model.access'
              AND name = ANY(%s)
        )
        """,
        (list(obsolete_xmlids),),
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
        WHERE module = 'almohit_core'
          AND model = 'ir.model.access'
          AND name = ANY(%s)
        """,
        (list(obsolete_xmlids),),
    )
