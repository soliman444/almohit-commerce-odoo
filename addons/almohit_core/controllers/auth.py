import json

from odoo import http
from odoo.http import request

from ..services.api import CORS_HEADERS, success, error
from ..services.customer import require_api_user, serialize_auth_user


class AlmohitAuthController(http.Controller):

    @http.route(
        "/almohit/api/auth/register",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def register(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )

        name = (payload.get("name") or "").strip()
        email = (payload.get("email") or "").strip().lower()
        password = payload.get("password") or ""
        phone = (payload.get("phone") or "").strip()
        role = (payload.get("role") or "merchant").strip().lower()

        if not name:
            return error("NAME_REQUIRED", "Name is required.", 400)

        if role not in ("merchant", "buyer", "distributor", "marketer"):
            return error(
                "INVALID_ROLE",
                "Role must be merchant, buyer, distributor, or marketer.",
                400,
            )

        if not email:
            return error("EMAIL_REQUIRED", "Email is required.", 400)

        if len(password) < 8:
            return error(
                "PASSWORD_TOO_SHORT",
                "Password must be at least 8 characters.",
                400,
            )

        Users = (
            request.env["res.users"]
            .sudo()
            .with_context(active_test=False)
        )

        existing_user = Users.search(
            [("login", "=", email)],
            limit=1,
        )

        if existing_user:
            return error(
                "EMAIL_ALREADY_EXISTS",
                "An account with this email already exists.",
                409,
            )

        company = request.env.ref(
            "base.main_company",
            raise_if_not_found=False,
        )

        if not company:
            company = request.env["res.company"].sudo().search([], limit=1)

        if not company:
            return error(
                "COMPANY_NOT_CONFIGURED",
                "No Odoo company is configured.",
                500,
            )

        portal_group = request.env.ref("base.group_portal")

        user = Users.create({
            "name": name,
            "login": email,
            "password": password,
            "company_id": company.id,
            "company_ids": [(6, 0, [company.id])],
            "groups_id": [(6, 0, [portal_group.id])],
        })

        partner = user.partner_id

        partner.write({
            "name": name,
            "email": email,
            "phone": phone or False,
            "almohit_trade_status": "normal",
            "almohit_app_role": role,
        })

        return success(serialize_auth_user(user, partner), status=201)

    @http.route(
        "/almohit/api/auth/login",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def login(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )

        email = (payload.get("email") or "").strip().lower()
        password = payload.get("password") or ""

        if not email or not password:
            return error(
                "INVALID_CREDENTIALS",
                "Email and password are required.",
                400,
            )

        try:
            uid = request.session.authenticate(
                request.db,
                email,
                password,
            )
        except Exception:
            uid = False

        if not uid:
            return error(
                "INVALID_CREDENTIALS",
                "Invalid email or password.",
                401,
            )

        user = request.env["res.users"].sudo().browse(uid)
        return success(serialize_auth_user(user, include_session=True))

    @http.route(
        "/almohit/api/auth/me",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def me(self, **kwargs):
        user, err = require_api_user()
        if err:
            return err
        return success(serialize_auth_user(user, include_session=True))

    @http.route(
        "/almohit/api/auth/logout",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def logout(self, **kwargs):
        user, err = require_api_user()
        if err:
            return err
        request.session.logout(keep_db=True)
        return success({
            "logged_out": True,
        })

    @http.route(
        "/almohit/api/<path:unused>",
        type="http",
        auth="none",
        methods=["OPTIONS"],
        csrf=False,
    )
    def cors_preflight(self, unused=None, **kwargs):
        return http.Response("", status=204, headers=CORS_HEADERS)
