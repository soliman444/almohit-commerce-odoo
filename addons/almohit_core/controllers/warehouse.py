from odoo import http

from ..services.api import read_json_body
from ..services.customer import require_warehouse_manager_partner
from ..services import warehouse as warehouse_service


class AlmohitWarehouseController(http.Controller):

    def _partner(self):
        return require_warehouse_manager_partner()

    @http.route("/almohit/api/warehouse/dashboard", type="http", auth="user", methods=["GET"], csrf=False)
    def dashboard(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.dashboard(partner)

    @http.route("/almohit/api/warehouse/my-warehouses", type="http", auth="user", methods=["GET"], csrf=False)
    def my_warehouses(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_my_warehouses(partner)

    @http.route("/almohit/api/warehouse/stock", type="http", auth="user", methods=["GET"], csrf=False)
    def stock(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_stock(partner, kwargs)

    @http.route("/almohit/api/warehouse/movements", type="http", auth="user", methods=["GET"], csrf=False)
    def movements(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_movements(partner, kwargs)

    # --- Receipts ---
    @http.route("/almohit/api/warehouse/receipts", type="http", auth="user", methods=["GET"], csrf=False)
    def list_receipts(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_receipts(partner, kwargs)

    @http.route("/almohit/api/warehouse/receipts/<int:picking_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_receipt(self, picking_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.get_receipt(partner, picking_id)

    @http.route("/almohit/api/warehouse/receipts/<int:picking_id>/receive", type="http", auth="user", methods=["POST"], csrf=False)
    def receive_receipt(self, picking_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.receive_receipt(partner, picking_id, payload)

    # --- Issues ---
    @http.route("/almohit/api/warehouse/issues", type="http", auth="user", methods=["GET"], csrf=False)
    def list_issues(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_issues(partner, kwargs)

    @http.route("/almohit/api/warehouse/issues/<int:picking_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_issue(self, picking_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.get_issue(partner, picking_id)

    @http.route("/almohit/api/warehouse/issues/<int:picking_id>/dispatch", type="http", auth="user", methods=["POST"], csrf=False)
    def dispatch_issue(self, picking_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.dispatch_issue(partner, picking_id, payload)

    # --- Transfers ---
    @http.route("/almohit/api/warehouse/transfers", type="http", auth="user", methods=["GET"], csrf=False)
    def list_transfers(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_transfers(partner, kwargs)

    @http.route("/almohit/api/warehouse/transfers", type="http", auth="user", methods=["POST"], csrf=False)
    def create_transfer(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.create_transfer(partner, payload)

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.get_transfer(partner, transfer_id)

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>/dispatch", type="http", auth="user", methods=["POST"], csrf=False)
    def dispatch_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.dispatch_transfer(partner, transfer_id, payload or {})

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>/receive", type="http", auth="user", methods=["POST"], csrf=False)
    def receive_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.receive_transfer(partner, transfer_id, payload)

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>/cancel", type="http", auth="user", methods=["POST"], csrf=False)
    def cancel_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            payload = {}
        return warehouse_service.cancel_transfer(partner, transfer_id, payload)

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>/reject", type="http", auth="user", methods=["POST"], csrf=False)
    def reject_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            payload = {}
        return warehouse_service.reject_transfer(partner, transfer_id, payload)

    @http.route("/almohit/api/warehouse/transfers/<int:transfer_id>/close", type="http", auth="user", methods=["POST"], csrf=False)
    def close_transfer(self, transfer_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            payload = {}
        return warehouse_service.close_transfer(partner, transfer_id, payload)

    @http.route("/almohit/api/warehouse/discrepancies/<int:discrepancy_id>/resolve", type="http", auth="user", methods=["POST"], csrf=False)
    def resolve_discrepancy(self, discrepancy_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            payload = {}
        return warehouse_service.resolve_discrepancy(partner, discrepancy_id, payload)

    @http.route(
        "/almohit/api/warehouse/stock-variances",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def stock_variances(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_stock_variances(
            partner, kwargs
        )

    # --- Inventory ---
    @http.route("/almohit/api/warehouse/inventory", type="http", auth="user", methods=["GET"], csrf=False)
    def list_inventory(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.list_inventory_sessions(partner, kwargs)

    @http.route("/almohit/api/warehouse/inventory/<int:session_id>", type="http", auth="user", methods=["GET"], csrf=False)
    def get_inventory(self, session_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        return warehouse_service.get_inventory_session(partner, session_id)

    @http.route("/almohit/api/warehouse/inventory/import", type="http", auth="user", methods=["POST"], csrf=False)
    def inventory_import(self, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            return body_err
        return warehouse_service.preview_inventory_import(partner, payload)

    @http.route("/almohit/api/warehouse/inventory/<int:session_id>/approve", type="http", auth="user", methods=["POST"], csrf=False)
    def inventory_approve(self, session_id, **kwargs):
        partner, err = self._partner()
        if err:
            return err
        payload, body_err = read_json_body()
        if body_err:
            payload = {}
        return warehouse_service.approve_inventory_session(partner, session_id, payload)
