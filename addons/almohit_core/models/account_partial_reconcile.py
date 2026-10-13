import logging

from odoo import api, models

from ..services.payments import (
    partial_applied_to_invoice,
    partial_is_customer_payment,
)
from ..services.whatsapp import (
    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
    EVENT_INVOICE_PAID,
    EVENT_PAYMENT_RECEIVED,
    schedule_notification,
)

_logger = logging.getLogger(__name__)


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def _almohit_out_invoices(self):
        moves = (
            self.mapped("debit_move_id.move_id")
            | self.mapped("credit_move_id.move_id")
        )
        return moves.filtered(
            lambda move: move.move_type == "out_invoice" and move.state == "posted"
        )

    @api.model_create_multi
    def create(self, vals_list):
        reconciles = super().create(vals_list)
        invoices = reconciles._almohit_out_invoices()
        try:
            invoices.invalidate_recordset(["payment_state", "amount_residual"])
        except Exception:
            _logger.exception(
                "whatsapp reconcile invalidate failed (reconciliation already created)"
            )
        for reconcile in reconciles:
            try:
                for invoice in reconcile._almohit_out_invoices():
                    order = invoice._almohit_storefront_invoice_order()
                    if not order:
                        continue
                    if not partial_is_customer_payment(reconcile, invoice):
                        continue
                    applied = partial_applied_to_invoice(reconcile, invoice)
                    if invoice.currency_id and invoice.currency_id.is_zero(applied):
                        continue
                    if not applied:
                        continue
                    schedule_notification(
                        EVENT_PAYMENT_RECEIVED,
                        invoice,
                        extra={
                            "applied_amount": applied,
                            "order": order,
                            "reconcile": reconcile,
                        },
                    )
            except Exception:
                _logger.exception(
                    "whatsapp payment_received failed reconcile=%s",
                    reconcile.id,
                )
        for invoice in invoices:
            try:
                order = invoice._almohit_storefront_invoice_order()
                if not order:
                    continue
                schedule_notification(
                    EVENT_DISTRIBUTION_REQUIREMENT_SATISFIED,
                    order,
                )
                if invoice.payment_state == "paid":
                    schedule_notification(
                        EVENT_INVOICE_PAID,
                        invoice,
                        extra={"order": order},
                    )
            except Exception:
                _logger.exception(
                    "whatsapp invoice reconcile follow-up failed invoice=%s",
                    invoice.id,
                )
        return reconciles