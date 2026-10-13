from odoo import http
from odoo.http import request

from ..services.api import error, read_json_body, success
from ..services.customer import require_marketer_partner
from ..services.trade import (
    APPLICATION_ALREADY_PENDING,
    COMPANY_NAME_REQUIRED,
    EMAIL_ALREADY_EXISTS,
    EMAIL_REQUIRED,
    INVALID_IMAGE,
    INVALID_PAYMENT_METHOD,
    NAME_REQUIRED,
    PASSWORD_TOO_SHORT,
    create_recruited_merchant,
    list_recruited_merchants,
    serialize_application,
)


class AlmohitMarketerController(http.Controller):

    @http.route(
        "/almohit/api/marketer/merchants",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_merchants(self, **kwargs):
        partner, err = require_marketer_partner()
        if err:
            return err
        return success(list_recruited_merchants(request.env, partner))

    @http.route(
        "/almohit/api/marketer/merchants",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_merchant(self, **kwargs):
        partner, err = require_marketer_partner()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err
        application, code = create_recruited_merchant(request.env, partner, payload)
        errors = {
            NAME_REQUIRED: ("NAME_REQUIRED", "Merchant name is required.", 400),
            EMAIL_REQUIRED: ("EMAIL_REQUIRED", "Merchant email is required.", 400),
            PASSWORD_TOO_SHORT: ("PASSWORD_TOO_SHORT", "Password must be at least 8 characters.", 400),
            EMAIL_ALREADY_EXISTS: ("EMAIL_ALREADY_EXISTS", "An account with this email already exists.", 409),
            COMPANY_NAME_REQUIRED: ("COMPANY_NAME_REQUIRED", "Shop name is required.", 400),
            INVALID_IMAGE: ("INVALID_IMAGE", "Shop image is missing or not allowed.", 400),
            INVALID_PAYMENT_METHOD: ("INVALID_PAYMENT_METHOD", "Payment method must be bank_transfer, cash, or both.", 400),
            APPLICATION_ALREADY_PENDING: ("APPLICATION_ALREADY_PENDING", "A pending trade application already exists.", 409),
            "COMPANY_NOT_CONFIGURED": ("COMPANY_NOT_CONFIGURED", "No Odoo company is configured.", 500),
        }
        if code:
            err_code, message, status = errors.get(code, (code, "Merchant could not be created.", 400))
            return error(err_code, message, status)
        return success(serialize_application(application), status=201)
