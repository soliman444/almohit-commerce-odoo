from odoo import http
from odoo.http import request

from ..services.api import error, read_json_body, success
from ..services.customer import require_customer_partner
from ..services.trade import (
    APPLICATION_ALREADY_APPROVED,
    APPLICATION_ALREADY_PENDING,
    COMPANY_NAME_REQUIRED,
    INVALID_IMAGE,
    INVALID_PAYMENT_METHOD,
    INVALID_ROLE,
    NAME_REQUIRED,
    PHONE_REQUIRED,
    create_own_application,
    get_latest_own_application,
    serialize_application,
)


class AlmohitTradeController(http.Controller):

    @http.route(
        "/almohit/api/trade/applications",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def create_application(self, **kwargs):
        # Portal users may submit before receiving any Almohit role group.
        partner, err = require_customer_partner()
        if err:
            return err
        payload, err = read_json_body()
        if err:
            return err

        extra_keys = set(payload) - {
            "company_name",
            "tax_number",
            "business_type",
            "phone",
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
            "role",
            "latitude",
            "longitude",
        }
        if extra_keys:
            return error(
                "INVALID_FIELDS",
                "One or more trade application fields are not allowed.",
                400,
            )

        account_role = (partner.almohit_app_role or "merchant").strip().lower()
        requested_role = payload.get("role")
        if requested_role not in (None, ""):
            if str(requested_role).strip().lower() != account_role:
                return error(
                    "INVALID_ROLE",
                    "Role must match the registered account role.",
                    400,
                )
        payload["role"] = account_role

        application, code = create_own_application(request.env, partner, payload)
        if code and not isinstance(code, str):
            return code
        if code == COMPANY_NAME_REQUIRED:
            return error(
                "COMPANY_NAME_REQUIRED",
                "Company name is required.",
                400,
            )
        if code == PHONE_REQUIRED:
            return error(
                "PHONE_REQUIRED",
                "Phone is required for warehouse manager applications.",
                400,
            )
        if code == NAME_REQUIRED:
            return error(
                "NAME_REQUIRED",
                "Name is required.",
                400,
            )
        if code == APPLICATION_ALREADY_PENDING:
            return error(
                "APPLICATION_ALREADY_PENDING",
                "A pending trade application already exists.",
                409,
            )
        if code == APPLICATION_ALREADY_APPROVED:
            return error(
                "APPLICATION_ALREADY_APPROVED",
                "An approved trade application already exists.",
                409,
            )
        if code == INVALID_IMAGE:
            return error(
                "INVALID_IMAGE",
                "Shop image is missing or not allowed.",
                400,
            )
        if code == INVALID_PAYMENT_METHOD:
            return error(
                "INVALID_PAYMENT_METHOD",
                "Payment method must be bank_transfer, cash, or both.",
                400,
            )
        if code == INVALID_ROLE:
            return error(
                "INVALID_ROLE",
                "Role must match the registered account role.",
                400,
            )

        return success(serialize_application(application), status=201)

    @http.route(
        "/almohit/api/trade/application",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_application(self, **kwargs):
        # Authenticated portal partner may read only their own latest application.
        partner, err = require_customer_partner()
        if err:
            return err
        application = get_latest_own_application(request.env, partner)
        return success(serialize_application(application))
