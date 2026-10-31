from datetime import datetime, time

from odoo.exceptions import UserError
from odoo.http import request

from .addresses import owned_address, serialize_address
from .api import error, parse_date_range_params, _query_scalar
from .cart import min_order_qty, quantity_below_moq, serialize_cart
from .customer import commercial_partner, serialize_payment_term
from .invoices import customer_invoice_ids, serialize_invoice_summary
from .payments import serialize_order_payment
from .stock import company_warehouse, insufficient_stock_error, serialize_delivery

CUSTOMER_ORDER_STATES = ("sale", "done", "sent")


def serialize_order(order):
    data = serialize_cart(order)
    data["name"] = order.name
    data["state"] = order.state
    data["date_order"] = order.date_order or None
    data["payment_term"] = serialize_payment_term(order.payment_term_id)
    shipping = order.partner_shipping_id.with_context(active_test=False)
    invoice = order.partner_invoice_id.with_context(active_test=False)
    data["shipping_address"] = serialize_address(shipping) if shipping else None
    data["invoice_address"] = serialize_address(invoice) if invoice else None
    data["delivery"] = serialize_delivery(order)
    invoices = customer_invoice_ids(order)
    data["invoice_status"] = order.invoice_status or None
    data["invoices"] = [serialize_invoice_summary(move) for move in invoices]
    data["payment"] = serialize_order_payment(invoices)
    for item in data.get("items") or []:
        item.pop("min_order_qty", None)
    return data


def confirmed_order_domain(partner):
    return [
        ("partner_id", "=", partner.id),
        ("almohit_storefront_order", "=", True),
        ("state", "not in", ("draft", "cancel")),
        ("company_id", "=", request.env.company.id),
    ]


def search_confirmed_orders(env, partner):
    return env["sale.order"].sudo().search(
        confirmed_order_domain(partner),
        order="id desc",
    )


def parse_order_list_params(kwargs):
    extra = []
    raw_q = _query_scalar(kwargs, "q")
    q = str(raw_q).strip() if raw_q not in (None, "") else ""
    if q:
        extra += [
            "|",
            ("name", "ilike", q),
            ("client_order_ref", "ilike", q),
        ]

    raw_state = _query_scalar(kwargs, "state")
    if raw_state not in (None, ""):
        state = str(raw_state).strip().lower()
        if state not in CUSTOMER_ORDER_STATES:
            return None, None, error(
                "INVALID_FILTER",
                "state must be one of: sale, done, sent.",
                400,
            )
        extra.append(("state", "=", state))

    date_from, date_to, err = parse_date_range_params(kwargs)
    if err:
        return None, None, err
    if date_from:
        extra.append(
            ("date_order", ">=", datetime.combine(date_from, time.min))
        )
    if date_to:
        extra.append(
            ("date_order", "<=", datetime.combine(date_to, time(23, 59, 59)))
        )

    order = "id desc"
    raw_sort = _query_scalar(kwargs, "sort")
    if raw_sort not in (None, ""):
        sort = str(raw_sort).strip().lower()
        if sort == "newest":
            order = "date_order desc, id desc"
        elif sort == "oldest":
            order = "date_order asc, id asc"
        else:
            return None, None, error(
                "INVALID_SORT",
                "sort must be one of: newest, oldest.",
                400,
            )
    return extra, order, None


def owned_confirmed_order(env, partner, order_id):
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return env["sale.order"].sudo().browse()
    return env["sale.order"].sudo().search(
        [("id", "=", order_id)] + confirmed_order_domain(partner),
        limit=1,
    )


ORDER_NOT_FOUND = "ORDER_NOT_FOUND"




def _checkout_lines(cart):
    return cart.order_line.filtered(lambda rec: not rec.display_type)


def _validate_line_products(cart):
    Product = cart.env["product.product"].sudo().with_context(active_test=False)
    for line in _checkout_lines(cart):
        product = Product.browse(line.product_id.id)
        if not product.exists():
            return (
                "PRODUCT_UNAVAILABLE",
                "This product is no longer available.",
                400,
                None,
            )
        template = product.product_tmpl_id
        if (
            not product.active
            or not template.active
            or not product.sale_ok
            or not template.sale_ok
        ):
            return (
                "PRODUCT_UNAVAILABLE",
                "This product is no longer available.",
                400,
                None,
            )
        moq = min_order_qty(product)
        if quantity_below_moq(line.product_uom_qty, moq):
            return (
                "MIN_ORDER_QUANTITY_NOT_MET",
                "Quantity is below the minimum order quantity.",
                400,
                {"min_order_qty": moq},
            )
        stock_error = insufficient_stock_error(
            line,
            product,
            warehouse=cart.warehouse_id,
        )
        if stock_error:
            return stock_error
    return None


def checkout_cart(partner, payload):
    commercial = commercial_partner(partner)
    env = request.env
    if not isinstance(payload, dict):
        payload = {}
    payload = dict(payload)
    payload.pop("partner_id", None)
    payload.pop("user_id", None)
    payload.pop("payment_term_id", None)
    payload.pop("price", None)
    payload.pop("subtotal", None)
    payload.pop("total", None)
    payload.pop("tax", None)
    payload.pop("currency", None)
    payload.pop("cart_id", None)
    payload.pop("order_id", None)

    shipping_id = payload.get("shipping_address_id")

    if shipping_id not in (None, "", False):
        try:
            shipping_id = int(shipping_id)
        except (TypeError, ValueError):
            return None, (
                "ADDRESS_NOT_FOUND",
                "Delivery address not found.",
                404,
                None,
            )
    else:
        shipping_id = None

    SaleOrder = env["sale.order"].sudo()
    cart = SaleOrder._almohit_find_cart(partner)
    if not cart:
        return None, (
            "CART_NOT_FOUND",
            "No storefront cart is available to check out.",
            404,
            None,
        )
    if not _checkout_lines(cart):
        return None, (
            "CART_EMPTY",
            "Cart is empty.",
            400,
            None,
        )
    if not cart._can_be_confirmed():
        return None, (
            "CART_ALREADY_CHECKED_OUT",
            "This cart has already been checked out.",
            409,
            None,
        )

    # The commercial partner is always the invoice address.
    invoice = commercial

    # The main commercial partner is also the default delivery address.
    shipping = commercial

    # If the merchant selected an additional delivery address,
    # it must belong to this commercial partner.
    if shipping_id:
        shipping = owned_address(
            env,
            commercial,
            shipping_id,
        )

        if not shipping:
            return None, (
                "ADDRESS_NOT_FOUND",
                "Delivery address not found.",
                404,
                None,
            )

        if shipping.type != "delivery":
            return None, (
                "ADDRESS_TYPE_INVALID",
                "shipping_address_id must be a delivery address.",
                400,
                None,
            )

    product_error = _validate_line_products(cart)
    if product_error:
        return None, product_error

    pricelist = partner.property_product_pricelist
    if not pricelist:
        return None, (
            "PRICELIST_REQUIRED",
            "No pricelist is assigned to this customer.",
            400,
            None,
        )
    if cart.pricelist_id != pricelist:
        cart.pricelist_id = pricelist.id

    # Native sale.order.payment_term_id computes from
    # res.partner.property_payment_term_id. Re-apply at confirm so a term
    # assigned after cart creation still flows. Empty term stays empty.
    term = partner.property_payment_term_id
    cart.write({
        "partner_shipping_id": shipping.id,
        "partner_invoice_id": invoice.id,
        "payment_term_id": term.id if term else False,
    })
    cart._recompute_prices()
    cart.invalidate_recordset()

    if cart.currency_id != pricelist.currency_id:
        return None, (
            "CURRENCY_MISMATCH",
            "Order currency does not match the customer pricelist.",
            400,
            None,
        )

    try:
        warehouse = company_warehouse(env)
    except UserError:
        return None, (
            "CHECKOUT_FAILED",
            "The order could not be confirmed.",
            400,
            None,
        )
    if not warehouse:
        return None, (
            "CHECKOUT_FAILED",
            "The order could not be confirmed.",
            400,
            None,
        )
    if cart.warehouse_id != warehouse:
        cart.warehouse_id = warehouse.id

    try:
        cart.action_confirm()
    except UserError:
        cart.invalidate_recordset()
        if cart.state not in ("draft", "sent"):
            return None, (
                "CART_ALREADY_CHECKED_OUT",
                "This cart has already been checked out.",
                409,
                None,
            )
        return None, (
            "CHECKOUT_FAILED",
            "The order could not be confirmed.",
            400,
            None,
        )

    cart.invalidate_recordset()
    return cart, None
