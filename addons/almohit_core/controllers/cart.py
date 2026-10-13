import json

from odoo import http
from odoo.http import request

from ..services.api import success, error
from ..services.cart import (
    min_order_qty,
    parse_quantity,
    quantity_below_moq,
    saleable_variant,
    serialize_cart,
    serialize_empty_cart,
)
from ..services.stock import company_warehouse, over_stock_error


class AlmohitCartController(http.Controller):

    @http.route(
        "/almohit/api/cart",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_cart(self, **kwargs):
        partner, err = self._require_approved_customer()
        if err:
            return err
        cart = self._carts().sudo()._almohit_find_cart(partner)
        if not cart:
            pricelist = partner.property_product_pricelist
            currency = (
                pricelist.currency_id
                if pricelist
                else request.env.company.currency_id
            )
            return success(serialize_empty_cart(currency))
        return success(serialize_cart(cart))

    @http.route(
        "/almohit/api/cart/items",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def add_item(self, **kwargs):
        partner, err = self._require_approved_customer()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        product_id = payload.get("product_id")
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return error(
                "PRODUCT_NOT_FOUND",
                "Product not found.",
                404,
            )

        quantity = parse_quantity(payload)
        if quantity is None or quantity <= 0:
            return error(
                "INVALID_QUANTITY",
                "Enter a valid quantity.",
                400,
            )

        product, reason = saleable_variant(request.env, product_id)
        if reason == "PRODUCT_NOT_FOUND":
            return error("PRODUCT_NOT_FOUND", "Product not found.", 404)
        if reason == "PRODUCT_UNAVAILABLE":
            return error(
                "PRODUCT_UNAVAILABLE",
                "This product is no longer available.",
                400,
            )

        moq = min_order_qty(product)
        cart = self._carts().sudo()._almohit_get_or_create_cart(partner)
        existing = cart.order_line.filtered(
            lambda line: not line.display_type and line.product_id.id == product.id
        )[:1]
        new_qty = (existing.product_uom_qty if existing else 0.0) + quantity
        if quantity_below_moq(new_qty, moq):
            return error(
                "MIN_ORDER_QUANTITY_NOT_MET",
                "Quantity is below the minimum order quantity.",
                400,
                extra={"min_order_qty": moq},
            )
        stock_err = over_stock_error(product, new_qty, company_warehouse(request.env))
        if stock_err:
            code, message, status, extra = stock_err
            return error(code, message, status, extra)

        if existing:
            existing.product_uom_qty = new_qty
        else:
            # Portal users cannot create sale.order.line. sudo() is scoped to
            # the current partner's storefront draft cart; price_unit is left
            # to native _compute_price_unit / _get_pricelist_price.
            request.env["sale.order.line"].sudo().create({
                "order_id": cart.id,
                "product_id": product.id,
                "product_uom_qty": new_qty,
            })
        cart.invalidate_recordset()
        return success(serialize_cart(cart))

    @http.route(
        "/almohit/api/cart/items/<int:line_id>",
        type="http",
        auth="user",
        methods=["PATCH"],
        csrf=False,
    )
    def update_item(self, line_id, **kwargs):
        partner, err = self._require_approved_customer()
        if err:
            return err
        payload, err = self._json_payload()
        if err:
            return err

        quantity = parse_quantity(payload)
        if quantity is None:
            return error(
                "INVALID_QUANTITY",
                "Enter a valid quantity.",
                400,
            )

        line = self._owned_cart_line(partner, line_id)
        if not line:
            return error(
                "CART_LINE_NOT_FOUND",
                "Cart line not found.",
                404,
            )

        if quantity <= 0:
            cart = line.order_id
            line.unlink()
            cart.invalidate_recordset()
            return success(serialize_cart(cart))

        moq = min_order_qty(line.product_id)
        if quantity_below_moq(quantity, moq):
            return error(
                "MIN_ORDER_QUANTITY_NOT_MET",
                "Quantity is below the minimum order quantity.",
                400,
                extra={"min_order_qty": moq},
            )
        stock_err = over_stock_error(line.product_id, quantity, company_warehouse(request.env))
        if stock_err:
            code, message, status, extra = stock_err
            return error(code, message, status, extra)

        line.product_uom_qty = quantity
        line.order_id.invalidate_recordset()
        return success(serialize_cart(line.order_id))

    @http.route(
        "/almohit/api/cart/items/<int:line_id>",
        type="http",
        auth="user",
        methods=["DELETE"],
        csrf=False,
    )
    def delete_item(self, line_id, **kwargs):
        partner, err = self._require_approved_customer()
        if err:
            return err
        line = self._owned_cart_line(partner, line_id)
        if not line:
            return error(
                "CART_LINE_NOT_FOUND",
                "Cart line not found.",
                404,
            )
        cart = line.order_id
        line.unlink()
        cart.invalidate_recordset()
        return success(serialize_cart(cart))

    @http.route(
        "/almohit/api/cart/clear",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def clear_cart(self, **kwargs):
        partner, err = self._require_approved_customer()
        if err:
            return err
        cart = self._carts().sudo()._almohit_find_cart(partner)
        if cart:
            cart.order_line.unlink()
            cart.invalidate_recordset()
            return success(serialize_cart(cart))
        pricelist = partner.property_product_pricelist
        currency = (
            pricelist.currency_id
            if pricelist
            else request.env.company.currency_id
        )
        return success(serialize_empty_cart(currency))

    def _require_approved_customer(self):
        user = request.env.user
        if not user.share:
            return None, error(
                "CUSTOMER_REQUIRED",
                "Only customers can access the cart.",
                403,
            )
        partner = user.partner_id
        if not partner:
            return None, error(
                "PARTNER_REQUIRED",
                "No customer partner is linked to this user.",
                400,
            )
        if partner.almohit_trade_status != "approved":
            return None, error(
                "WHOLESALE_APPROVAL_REQUIRED",
                "Wholesale approval is required to use the cart.",
                403,
            )
        if (partner.almohit_app_role or "merchant") == "marketer":
            return None, error(
                "MARKETER_CANNOT_ORDER",
                "Marketers cannot place orders.",
                403,
            )
        # Portal users cannot create/write sale.order. Further cart ORM
        # calls use sudo() but remain scoped to this session partner.
        return partner.sudo(), None

    def _carts(self):
        return request.env["sale.order"]

    def _owned_cart_line(self, partner, line_id):
        return request.env["sale.order.line"].sudo().search(
            [
                ("id", "=", line_id),
                ("display_type", "=", False),
                ("order_id.partner_id", "=", partner.id),
                ("order_id.state", "=", "draft"),
                ("order_id.almohit_storefront_order", "=", True),
                ("order_id.company_id", "=", request.env.company.id),
            ],
            limit=1,
        )

    def _json_payload(self):
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return None, error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )
        if not isinstance(payload, dict):
            return None, error(
                "INVALID_JSON",
                "Invalid JSON body.",
                400,
            )
        return payload, None
