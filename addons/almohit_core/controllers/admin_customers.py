from odoo import http
from odoo.http import request

from ..services.admin_customers import (
    admin_customer_summary,
    admin_staff_summary,
    list_admin_customers,
    list_admin_staff,
)
from ..services.api import success
from ..services.staff import require_internal_staff


class AlmohitAdminCustomerController(http.Controller):

    @http.route(
        "/almohit/api/admin/customers/summary",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def summary(self, **kwargs):
        _user, err = require_internal_staff("read", "res.partner")
        if err:
            return err
        return success(admin_customer_summary(request.env))

    @http.route(
        "/almohit/api/admin/customers",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_customers(self, **kwargs):
        _user, err = require_internal_staff("read", "res.partner")
        if err:
            return err
        return success(list_admin_customers(request.env, kwargs))

    @http.route(
        "/almohit/api/admin/staff/summary",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def staff_summary(self, **kwargs):
        _user, err = require_internal_staff("read", "res.users")
        if err:
            return err
        return success(admin_staff_summary(request.env))

    @http.route(
        "/almohit/api/admin/staff",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_staff(self, **kwargs):
        _user, err = require_internal_staff("read", "res.users")
        if err:
            return err
        return success(list_admin_staff(request.env, kwargs))
