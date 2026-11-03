"""POST /transfers creates + dispatches stock→transit atomically."""
import json
import threading

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.almohit_core.services import warehouse_transfers


def _body(response):
    return json.loads(response.data.decode("utf-8"))


@tagged("post_install", "-at_install", "almohit_warehouse")
class TestWarehouseTransferCreateDispatch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Warehouse = cls.env["stock.warehouse"].sudo()
        cls.wh_a = Warehouse.search([("company_id", "=", cls.company.id)], limit=1)
        cls.wh_b = Warehouse.create({
            "name": "Almohit Transfer WH B",
            "code": "XTB",
            "company_id": cls.company.id,
        })
        cls.wh_other = Warehouse.create({
            "name": "Almohit Transfer WH Other",
            "code": "XTO",
            "company_id": cls.company.id,
        })
        cls.product = cls.env["product.product"].create({
            "name": "Almohit Transfer Apple",
            "type": "product",
            "uom_id": cls.env.ref("uom.product_uom_unit").id,
            "uom_po_id": cls.env.ref("uom.product_uom_unit").id,
        })
        Quant = cls.env["stock.quant"].sudo()
        Quant._update_available_quantity(cls.product, cls.wh_a.lot_stock_id, 30.0)

        cls.manager = cls.env["res.partner"].create({
            "name": "Almohit Transfer Manager",
            "almohit_app_role": "warehouse_manager",
        })
        cls.assignment = cls.env["almohit.warehouse.assignment"].create({
            "partner_id": cls.manager.id,
            "warehouse_ids": [(6, 0, [cls.wh_a.id, cls.wh_b.id])],
            "default_warehouse_id": cls.wh_a.id,
            "perm_transfer_create": True,
            "perm_transfer_receive": True,
            "perm_stock_read": True,
        })

    def _create(self, payload):
        return _body(warehouse_transfers.create_transfer(self.manager, payload))

    def test_create_and_dispatch_success(self):
        before = self.env["stock.quant"].sudo()._get_available_quantity(
            self.product, self.wh_a.lot_stock_id
        )
        payload = self._create({
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "lines": [{"product_id": self.product.id, "quantity": 5.0}],
        })
        self.assertTrue(payload.get("ok"), payload)
        data = payload["data"]
        self.assertEqual(data["state"], "dispatched")
        self.assertTrue(data.get("dispatch_picking_id"))
        transfer = self.env["almohit.warehouse.transfer"].browse(data["id"])
        self.assertEqual(transfer.state, "dispatched")
        self.assertTrue(transfer.dispatch_picking_id)
        self.assertEqual(transfer.dispatch_picking_id.state, "done")
        after = self.env["stock.quant"].sudo()._get_available_quantity(
            self.product, self.wh_a.lot_stock_id
        )
        self.assertEqual(after, before - 5.0)
        # Still awaiting destination receive — not complete.
        self.assertNotIn(transfer.state, ("received", "closed"))

    def test_dispatch_failure_rolls_back_create(self):
        # Force missing internal type on source for dispatch failure after create path.
        # Use a warehouse with stock but patch int_type temporarily via unlink-safe false.
        source = self.wh_a
        original = source.int_type_id
        Transfer = self.env["almohit.warehouse.transfer"].sudo()
        before_count = Transfer.search_count([])
        # Break internal type reference for this call by writing False (may be required).
        # If Odoo blocks empty int_type, simulate via monkeypatch of helper.
        def boom(*_a, **_k):
            from odoo.exceptions import UserError
            raise UserError("forced dispatch failure")

        original_core = warehouse_transfers._dispatch_transfer_core
        warehouse_transfers._dispatch_transfer_core = boom
        try:
            payload = self._create({
                "source_warehouse_id": self.wh_a.id,
                "dest_warehouse_id": self.wh_b.id,
                "lines": [{"product_id": self.product.id, "quantity": 2.0}],
            })
        finally:
            warehouse_transfers._dispatch_transfer_core = original_core
            source.int_type_id = original

        self.assertFalse(payload.get("ok"), payload)
        self.assertEqual(Transfer.search_count([]), before_count)
        # Stock unchanged.
        self.assertEqual(
            self.env["stock.quant"].sudo()._get_available_quantity(
                self.product, self.wh_a.lot_stock_id
            ),
            30.0,
        )

    def test_insufficient_stock(self):
        payload = self._create({
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "lines": [{"product_id": self.product.id, "quantity": 999.0}],
        })
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload["error"]["code"], "INSUFFICIENT_STOCK")

    def test_unassigned_destination_warehouse(self):
        payload = self._create({
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_other.id,
            "lines": [{"product_id": self.product.id, "quantity": 1.0}],
        })
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload["error"]["code"], "WAREHOUSE_NOT_ALLOWED")

    def test_idempotent_retry(self):
        body = {
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "idempotency_key": "xfer-idem-1",
            "lines": [{"product_id": self.product.id, "quantity": 3.0}],
        }
        first = self._create(body)
        self.assertTrue(first.get("ok"), first)
        second = self._create(body)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first["data"]["id"], second["data"]["id"])
        self.assertEqual(
            self.env["almohit.warehouse.transfer"].sudo().search_count([
                ("idempotency_key", "=", "xfer-idem-1"),
            ]),
            1,
        )

    def test_concurrent_same_idempotency_key(self):
        import hashlib

        body = {
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "idempotency_key": "xfer-concurrent-1",
            "lines": [{"product_id": self.product.id, "quantity": 2.0}],
        }
        lock_material = "transfer-idem:%s:%s" % (self.company.id, "xfer-concurrent-1")
        lock_key = int(hashlib.sha1(lock_material.encode("utf-8")).hexdigest()[:8], 16)
        lock_held = threading.Event()
        stop_hold = threading.Event()
        hold_error = []

        def hold_lock():
            try:
                with self.env.registry.cursor() as cr:
                    cr.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)",
                        (87110426, int(lock_key) & 0x7FFFFFFF),
                    )
                    lock_held.set()
                    stop_hold.wait(timeout=30)
            except Exception as exc:  # noqa: BLE001
                hold_error.append(exc)
                lock_held.set()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(lock_held.wait(timeout=10), hold_error)
        try:
            busy = self._create(body)
            self.assertFalse(busy.get("ok"), busy)
            self.assertEqual(busy["error"]["code"], "TRANSFER_BUSY")
        finally:
            stop_hold.set()
            holder.join(timeout=30)

        first = self._create(body)
        self.assertTrue(first.get("ok"), first)
        second = self._create(body)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first["data"]["id"], second["data"]["id"])
        self.assertEqual(
            self.env["almohit.warehouse.transfer"].sudo().search_count([
                ("idempotency_key", "=", "xfer-concurrent-1"),
            ]),
            1,
        )

    def test_stays_in_transit_until_receive(self):
        created = self._create({
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "lines": [{"product_id": self.product.id, "quantity": 4.0}],
        })
        self.assertTrue(created.get("ok"), created)
        transfer = self.env["almohit.warehouse.transfer"].browse(created["data"]["id"])
        self.assertEqual(transfer.state, "dispatched")
        self.assertFalse(transfer.receive_picking_id)

        received = _body(warehouse_transfers.receive_transfer(
            self.manager,
            transfer.id,
            {"lines": [{"line_id": transfer.line_ids[:1].id, "quantity": 4.0}]},
        ))
        self.assertTrue(received.get("ok"), received)
        transfer.invalidate_recordset()
        self.assertEqual(transfer.state, "received")

    def test_partial_receive_after_dispatch(self):
        created = self._create({
            "source_warehouse_id": self.wh_a.id,
            "dest_warehouse_id": self.wh_b.id,
            "lines": [{"product_id": self.product.id, "quantity": 6.0}],
        })
        self.assertTrue(created.get("ok"), created)
        transfer = self.env["almohit.warehouse.transfer"].browse(created["data"]["id"])
        line_id = transfer.line_ids[:1].id
        partial = _body(warehouse_transfers.receive_transfer(
            self.manager,
            transfer.id,
            {"lines": [{"line_id": line_id, "quantity": 4.0, "reason": "missing"}]},
        ))
        self.assertTrue(partial.get("ok"), partial)
        transfer.invalidate_recordset()
        self.assertIn(transfer.state, ("partially_received", "discrepancy"))
        self.assertEqual(float(transfer.line_ids[:1].qty_received), 4.0)
        self.assertEqual(float(transfer.line_ids[:1].qty_dispatched), 6.0)
