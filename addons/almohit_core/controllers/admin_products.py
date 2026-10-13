from odoo import http
from odoo.http import request

from ..services.admin_products import (
    create_admin_product,
    get_admin_product,
    list_admin_product_options,
    list_admin_products,
)
from ..services.api import read_json_body, success
from ..services.staff import require_internal_staff


class AlmohitAdminProductsController(http.Controller):

    @http.route(
        "/almohit/api/admin/product-options",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def product_options(self, **kwargs):
        _user, err = require_internal_staff("read")
        if err:
            return err
        return success(list_admin_product_options(request.env))

    @http.route(
        "/almohit/api/admin/products",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_products(self, **kwargs):
        _user, err = require_internal_staff("read")
        if err:
            return err
        return success(list_admin_products(request.env, kwargs))

    @http.route(
        "/almohit/api/admin/products/<int:product_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_product(self, product_id, **kwargs):
        _user, err = require_internal_staff("read")
        if err:
            return err
        data, err = get_admin_product(request.env, product_id)
        if err:
            return err
        return success(data)

    @http.route(
        "/almohit/api/admin/products",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_product(self, **kwargs):
        _user, err = require_internal_staff("create")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, err = create_admin_product(request.env, payload)
        if err:
            return err
        return success(data, status=201)
