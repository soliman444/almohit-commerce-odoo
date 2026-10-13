from odoo.exceptions import UserError
from odoo.http import request

from .addresses import owned_address, serialize_address
from .cart import min_order_qty, quantity_below_moq, serialize_cart
from .customer import commercial_partner, serialize_payment_term
from .invoices import customer_invoice_ids, serialize_invoice_summary
from .payments import serialize_order_payment
from .stock import insufficient_stock_error, serialize_delivery


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
ADMIN_ORDER_STATES = {"sale", "done", "cancel", "sent"}
ADMIN_PAYMENT_STATES = {"paid", "partial", "not_paid", "reversed"}


def admin_storefront_order_domain():
    return [
        ("almohit_storefront_order", "=", True),
        ("state", "!=", "draft"),
        ("company_id", "=", request.env.company.id),
    ]


def serialize_admin_order(order):
    data = serialize_order(order)
    partner = order.partner_id
    commercial = partner.commercial_partner_id or partner
    data["customer"] = {
        "partner_id": commercial.id,
        "name": commercial.name or partner.name,
        "email": commercial.email or partner.email or None,
    }
    data["item_count"] = int(sum(
        line.product_uom_qty
        for line in order.order_line.filtered(lambda rec: not rec.display_type)
    ))
    data["note"] = order.note or None
    return data


def list_admin_orders(env, params=None):
    params = params or {}
    domain = admin_storefront_order_domain()
    state = (params.get("state") or "").strip().lower()
    if state in ADMIN_ORDER_STATES:
        domain.append(("state", "=", state))
    query = (params.get("q") or "").strip()
    if query:
        domain.extend([
            "|",
            "|",
            "|",
            ("name", "ilike", query),
            ("partner_id.name", "ilike", query),
            ("partner_id.email", "ilike", query),
            ("client_order_ref", "ilike", query),
        ])
    records = env["sale.order"].search(domain, order="id desc", limit=100)
    rows = [serialize_admin_order(order) for order in records]
    payment = (params.get("payment") or "").strip().lower()
    if payment in ADMIN_PAYMENT_STATES:
        rows = [
            row
            for row in rows
            if (row.get("payment") or {}).get("state") == payment
        ]
    return rows


def get_admin_order(env, order_id):
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return None
    order = env["sale.order"].search(
        [("id", "=", order_id)] + admin_storefront_order_domain(),
        limit=1,
    )
    if not order:
        return None
    return serialize_admin_order(order)


def admin_order_summary(env):
    return {"total": env["sale.order"].search_count(admin_storefront_order_domain())}


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
    invoice_id = payload.get("invoice_address_id")
    try:
        shipping_id = int(shipping_id)
        invoice_id = int(invoice_id)
    except (TypeError, ValueError):
        return None, (
            "ADDRESS_NOT_FOUND",
            "Address not found.",
            404,
            None,
        )

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

    shipping = owned_address(env, commercial, shipping_id)
    invoice = owned_address(env, commercial, invoice_id)
    if not shipping or not invoice:
        return None, (
            "ADDRESS_NOT_FOUND",
            "Address not found.",
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
    if invoice.type != "invoice":
        return None, (
            "ADDRESS_TYPE_INVALID",
            "invoice_address_id must be an invoice address.",
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
