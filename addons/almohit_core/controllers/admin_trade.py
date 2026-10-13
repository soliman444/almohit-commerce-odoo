from odoo import http
from odoo.http import request

from ..services.api import error, read_json_body, success
from ..services.staff import require_internal_staff
from ..services.trade import (
    APPLICATION_NOT_FOUND,
    APPLICATION_NOT_PENDING,
    approve_admin_application,
    list_admin_applications,
    reject_admin_application,
)


class AlmohitAdminTradeController(http.Controller):

    @http.route(
        "/almohit/api/admin/trade/applications",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_applications(self, **kwargs):
        _user, err = require_internal_staff("read", "almohit.trade.application")
        if err:
            return err
        return success(list_admin_applications(request.env, kwargs))

    @http.route(
        "/almohit/api/admin/trade/applications/<int:application_id>/approve",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def approve_application(self, application_id, **kwargs):
        _user, err = require_internal_staff("write", "almohit.trade.application")
        if err:
            return err
        data, code = approve_admin_application(request.env, application_id)
        if code == APPLICATION_NOT_FOUND:
            return error("APPLICATION_NOT_FOUND", "Trade application not found.", 404)
        if code == APPLICATION_NOT_PENDING:
            return error(
                "APPLICATION_NOT_PENDING",
                "Only pending trade applications can be approved.",
                409,
            )
        return success(data)

    @http.route(
        "/almohit/api/admin/trade/applications/<int:application_id>/reject",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def reject_application(self, application_id, **kwargs):
        _user, err = require_internal_staff("write", "almohit.trade.application")
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        data, code = reject_admin_application(
            request.env,
            application_id,
            payload.get("reason") or "",
        )
        if code == APPLICATION_NOT_FOUND:
            return error("APPLICATION_NOT_FOUND", "Trade application not found.", 404)
        if code == APPLICATION_NOT_PENDING:
            return error(
                "APPLICATION_NOT_PENDING",
                "Only pending trade applications can be rejected.",
                409,
            )
        return success(data)
