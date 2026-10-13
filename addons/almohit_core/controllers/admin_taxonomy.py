from odoo import http
from odoo.http import request

from ..services.admin_taxonomy import (
    add_admin_attribute_values,
    catalog_stats,
    create_admin_attribute,
    create_admin_brand,
    create_admin_category,
    list_admin_attributes,
    list_admin_brands,
    list_admin_categories,
)
from ..services.api import read_json_body, success
from ..services.staff import require_internal_staff


class AlmohitAdminTaxonomyController(http.Controller):

    @http.route(
        "/almohit/api/admin/catalog-stats",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def stats(self, **kwargs):
        _user, err = require_internal_staff("read", "product.template")
        if err:
            return err
        return success(catalog_stats(request.env))

    @http.route(
        "/almohit/api/admin/categories",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_categories(self, **kwargs):
        _user, err = require_internal_staff("read", "product.category")
        if err:
            return err
        return success(list_admin_categories(request.env))

    @http.route(
        "/almohit/api/admin/categories",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_category(self, **kwargs):
        _user, err = require_internal_staff("create", "product.category")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, err = create_admin_category(request.env, payload)
        if err:
            return err
        return success(data, status=201)

    @http.route(
        "/almohit/api/admin/brands",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_brands(self, **kwargs):
        _user, err = require_internal_staff("read", "almohit.product.brand")
        if err:
            return err
        return success(list_admin_brands(request.env))

    @http.route(
        "/almohit/api/admin/brands",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_brand(self, **kwargs):
        _user, err = require_internal_staff("create", "almohit.product.brand")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, err = create_admin_brand(request.env, payload)
        if err:
            return err
        return success(data, status=201)

    @http.route(
        "/almohit/api/admin/attributes",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_attributes(self, **kwargs):
        _user, err = require_internal_staff("read", "product.attribute")
        if err:
            return err
        return success(list_admin_attributes(request.env))

    @http.route(
        "/almohit/api/admin/attributes",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_attribute(self, **kwargs):
        _user, err = require_internal_staff("create", "product.attribute")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, err = create_admin_attribute(request.env, payload)
        if err:
            return err
        return success(data, status=201)

    @http.route(
        "/almohit/api/admin/attributes/<int:attribute_id>/values",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def add_attribute_values(self, attribute_id, **kwargs):
        _user, err = require_internal_staff("create", "product.attribute.value")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, err = add_admin_attribute_values(request.env, attribute_id, payload)
        if err:
            return err
        return success(data, status=201)
