from odoo import http

from ..services.api import read_json_body
from ..services.customer import require_buyer_user
from ..services import buyer as buyer_service


class AlmohitBuyerController(http.Controller):

    @http.route(
        "/almohit/api/buyer/suppliers",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_suppliers(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.list_suppliers(user, kwargs)

    @http.route(
        "/almohit/api/buyer/suppliers",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_supplier(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.create_supplier(user, payload)

    @http.route(
        "/almohit/api/buyer/suppliers/<int:supplier_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_supplier(self, supplier_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.get_supplier(user, supplier_id)

    @http.route(
        "/almohit/api/buyer/suppliers/<int:supplier_id>",
        type="http",
        auth="user",
        methods=["PATCH"],
        csrf=False,
    )
    def patch_supplier(self, supplier_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.patch_supplier(user, supplier_id, payload)

    @http.route(
        "/almohit/api/buyer/products",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_products(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.list_buyer_products(user, kwargs)

    @http.route(
        "/almohit/api/buyer/products",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_product(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.create_buyer_product(user, payload)

    @http.route(
        "/almohit/api/buyer/products/<int:product_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_product(self, product_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.get_buyer_product(user, product_id)

    @http.route(
        "/almohit/api/buyer/purchases",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_purchases(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.list_purchases(user, kwargs)

    @http.route(
        "/almohit/api/buyer/purchases",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_purchase(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.create_purchase(user, payload)

    @http.route(
        "/almohit/api/buyer/purchases/<int:purchase_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_purchase(self, purchase_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.get_purchase(user, purchase_id)

    @http.route(
        "/almohit/api/buyer/purchases/<int:purchase_id>",
        type="http",
        auth="user",
        methods=["PATCH"],
        csrf=False,
    )
    def patch_purchase(self, purchase_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.patch_purchase(user, purchase_id, payload)

    @http.route(
        "/almohit/api/buyer/purchases/<int:purchase_id>/confirm",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def confirm_purchase(self, purchase_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.confirm_purchase(user, purchase_id)

    @http.route(
        "/almohit/api/buyer/receipts",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_receipts(self, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.list_receipts(user, kwargs)

    @http.route(
        "/almohit/api/buyer/receipts/<int:picking_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_receipt(self, picking_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        return buyer_service.get_receipt(user, picking_id)

    @http.route(
        "/almohit/api/buyer/receipts/<int:picking_id>/receive",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def receive_receipt(self, picking_id, **kwargs):
        user, err = require_buyer_user()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        return buyer_service.receive_receipt(user, picking_id, payload)
