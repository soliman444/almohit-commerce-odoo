from odoo import http
from odoo.http import request

from ..services.api import error, paginated_success, parse_pagination, read_json_body, success
from ..services.customer import require_almohit_group, require_marketer_partner
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
    parse_merchant_list_params,
    search_recruited_merchants,
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
        err = require_almohit_group(
            "almohit_core.group_almohit_marketer",
        )
        if err:
            return err
        partner, err = require_marketer_partner()
        if err:
            return err
        page, page_size, err = parse_pagination(kwargs)
        if err:
            return err
        extra, order, err = parse_merchant_list_params(kwargs)
        if err:
            return err
        merchants = search_recruited_merchants(request.env, partner)
        merchants = merchants.search(
            [("id", "in", merchants.ids)] + extra,
            order=order,
        )
        return paginated_success(
            merchants,
            serialize_application,
            page,
            page_size,
        )

    @http.route(
        "/almohit/api/marketer/merchants",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_merchant(self, **kwargs):
        err = require_almohit_group(
            "almohit_core.group_almohit_marketer",
        )
        if err:
            return err
        partner, err = require_marketer_partner()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err

        extra_keys = set(payload) - {
            "name",
            "email",
            "password",
            "phone",
            "company_name",
            "tax_number",
            "business_type",
            "street",
            "address",
            "city",
            "manager_name",
            "employee_name",
            "bank_account",
            "payment_method",
            "notes",
            "shop_image_base64",
            "shop_image_content_type",
        }
        if extra_keys:
            return error(
                "INVALID_FIELDS",
                "One or more merchant fields are not allowed.",
                400,
            )

        application, code = create_recruited_merchant(request.env, partner, payload)
        if code and not isinstance(code, str):
            return code
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
