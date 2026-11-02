def migrate(cr, version):
    """Default purchase receipt validation to warehouse managers only."""
    cr.execute(
        """
        INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date)
        SELECT
            'almohit.receipt_validation_mode',
            'warehouse_only',
            1,
            NOW() AT TIME ZONE 'UTC',
            1,
            NOW() AT TIME ZONE 'UTC'
        WHERE NOT EXISTS (
            SELECT 1 FROM ir_config_parameter
            WHERE key = 'almohit.receipt_validation_mode'
        )
        """
    )
    cr.execute(
        """
        UPDATE ir_config_parameter
        SET value = 'warehouse_only',
            write_date = NOW() AT TIME ZONE 'UTC'
        WHERE key = 'almohit.receipt_validation_mode'
          AND COALESCE(value, '') IN ('', 'both')
        """
    )
