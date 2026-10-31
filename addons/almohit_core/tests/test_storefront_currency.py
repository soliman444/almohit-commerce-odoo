from unittest.mock import MagicMock, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.almohit_core.services.storefront_currency import (
    StorefrontCurrencyError,
    active_currency_by_code,
    authorized_currency_pricelists,
    requested_currency,
    storefront_pricelist,
)


@tagged("post_install", "-at_install", "almohit_currency")
class TestStorefrontCurrency(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usd = cls.env.ref("base.USD")
        cls.sar = cls.env.ref("base.SAR")
        cls.yer = cls.env.ref("base.YER", raise_if_not_found=False)
        cls.company = cls.env.company
        # Ensure storefront currencies are active for resolution tests.
        (cls.usd | cls.sar).write({"active": True})
        if cls.yer:
            cls.yer.active = True

        cls.list_usd = cls.env["product.pricelist"].create({
            "name": "Almohit Test Wholesale USD",
            "currency_id": cls.usd.id,
            "company_id": cls.company.id,
            "sequence": 20,
        })
        cls.list_sar = cls.env["product.pricelist"].create({
            "name": "Almohit Test Wholesale SAR",
            "currency_id": cls.sar.id,
            "company_id": cls.company.id,
            "sequence": 21,
        })
        cls.list_sar_other = cls.env["product.pricelist"].create({
            "name": "Other Segment SAR",
            "currency_id": cls.sar.id,
            "company_id": cls.company.id,
            "sequence": 22,
        })
        cls.list_usd.almohit_currency_variant_ids = [(6, 0, [cls.list_sar.id])]

        cls.partner = cls.env["res.partner"].create({
            "name": "Currency Merchant",
            "almohit_trade_status": "approved",
            "almohit_app_role": "merchant",
            "property_product_pricelist": cls.list_usd.id,
        })
        cls.partner_pending = cls.env["res.partner"].create({
            "name": "Pending Merchant",
            "almohit_trade_status": "pending",
            "almohit_app_role": "merchant",
            "property_product_pricelist": cls.list_usd.id,
        })
        cls.partner_other = cls.env["res.partner"].create({
            "name": "Other Segment Merchant",
            "almohit_trade_status": "approved",
            "almohit_app_role": "merchant",
            "property_product_pricelist": cls.list_sar_other.id,
        })

    def test_active_currency_usd_sar(self):
        self.assertEqual(active_currency_by_code(self.env, "usd").id, self.usd.id)
        self.assertEqual(active_currency_by_code(self.env, "SAR").id, self.sar.id)

    def test_unknown_currency_xyz(self):
        with self.assertRaises(StorefrontCurrencyError):
            active_currency_by_code(self.env, "XYZ")

    def test_inactive_currency(self):
        inactive = self.env["res.currency"].with_context(active_test=False).search(
            [("active", "=", False)],
            limit=1,
        )
        if not inactive:
            self.skipTest("No inactive currency in DB")
        with self.assertRaises(StorefrontCurrencyError):
            active_currency_by_code(self.env, inactive.name)

    def test_no_header_keeps_base_pricelist(self):
        pricelist = storefront_pricelist(self.env, self.partner, None)
        self.assertEqual(pricelist.id, self.list_usd.id)

    def test_same_currency_keeps_base(self):
        pricelist = storefront_pricelist(self.env, self.partner, self.usd)
        self.assertEqual(pricelist.id, self.list_usd.id)

    def test_linked_sar_variant(self):
        pricelist = storefront_pricelist(self.env, self.partner, self.sar)
        self.assertEqual(pricelist.id, self.list_sar.id)

    def test_unlinked_currency_rejected(self):
        if not self.yer:
            self.skipTest("YER currency missing")
        self.yer.active = True
        with self.assertRaises(StorefrontCurrencyError) as ctx:
            storefront_pricelist(self.env, self.partner, self.yer)
        self.assertIn("No authorized pricelist", str(ctx.exception))

    def test_other_segment_sar_not_usable_by_wholesale_merchant(self):
        # Even though list_sar_other is SAR, it is not linked to list_usd.
        matches = authorized_currency_pricelists(self.env, self.list_usd, self.sar)
        self.assertEqual(matches.ids, [self.list_sar.id])
        self.assertNotIn(self.list_sar_other.id, matches.ids)

    def test_multiple_authorized_sar_rejected(self):
        second_sar = self.env["product.pricelist"].create({
            "name": "Almohit Test Wholesale SAR 2",
            "currency_id": self.sar.id,
            "company_id": self.company.id,
        })
        self.list_usd.almohit_currency_variant_ids = [
            (6, 0, [self.list_sar.id, second_sar.id]),
        ]
        with self.assertRaises(StorefrontCurrencyError) as ctx:
            storefront_pricelist(self.env, self.partner, self.sar)
        self.assertIn("Multiple authorized pricelists", str(ctx.exception))

    def test_header_priority_over_query(self):
        mock_request = MagicMock()
        mock_request.httprequest.headers.get.return_value = "SAR"
        with patch(
            "odoo.addons.almohit_core.services.storefront_currency.request",
            mock_request,
        ):
            currency = requested_currency(self.env, query_code="USD")
        self.assertEqual(currency.id, self.sar.id)

    def test_query_currency_when_no_header(self):
        mock_request = MagicMock()
        mock_request.httprequest.headers.get.return_value = None
        with patch(
            "odoo.addons.almohit_core.services.storefront_currency.request",
            mock_request,
        ):
            currency = requested_currency(self.env, query_code="usd")
        self.assertEqual(currency.id, self.usd.id)

    def test_cart_pricelist_switch_draft_only(self):
        cart = self.env["sale.order"].sudo()._almohit_get_or_create_cart(self.partner)
        self.assertEqual(cart.pricelist_id.id, self.list_usd.id)
        cart._almohit_set_cart_pricelist(self.list_sar)
        self.assertEqual(cart.pricelist_id.id, self.list_sar.id)
        self.assertEqual(cart.currency_id.id, self.sar.id)

        cart.action_confirm()
        with self.assertRaises(ValueError):
            cart._almohit_set_cart_pricelist(self.list_usd)

    def test_invoice_currency_follows_order(self):
        product = self.env["product.product"].create({
            "name": "Currency Test Product",
            "type": "consu",
            "list_price": 10.0,
            "sale_ok": True,
        })
        cart = self.env["sale.order"].sudo()._almohit_get_or_create_cart(self.partner)
        cart._almohit_set_cart_pricelist(self.list_sar)
        self.env["sale.order.line"].sudo().create({
            "order_id": cart.id,
            "product_id": product.id,
            "product_uom_qty": 1.0,
        })
        cart.action_confirm()
        moves = cart._create_invoices()
        self.assertTrue(moves)
        self.assertEqual(moves.currency_id.id, cart.currency_id.id)
        self.assertEqual(moves.currency_id.id, self.sar.id)

    def test_no_partner_pricelist(self):
        partner = self.env["res.partner"].create({
            "name": "No List",
            "property_product_pricelist": False,
        })
        # Odoo may still assign a default company pricelist via commercial fields.
        if partner.property_product_pricelist:
            partner.property_product_pricelist = False
        if partner.property_product_pricelist:
            self.skipTest("Database forces a default partner pricelist")
        with self.assertRaises(StorefrontCurrencyError):
            storefront_pricelist(self.env, partner, self.usd)
