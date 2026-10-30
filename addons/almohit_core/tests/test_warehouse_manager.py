import base64
import csv
import io
import zipfile

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.almohit_core.services.warehouse_common import (
    ICP_RECEIPT_MODE,
    filter_assignment_warehouses,
    receipt_validation_mode,
)
from odoo.addons.almohit_core.services import warehouse_inventory
from odoo.addons.almohit_core.services import warehouse_transfers


@tagged("post_install", "-at_install", "almohit_warehouse")
class TestWarehouseManagerScaffold(TransactionCase):
    def test_receipt_mode_default_both(self):
        self.env["ir.config_parameter"].sudo().set_param(ICP_RECEIPT_MODE, False)
        self.assertEqual(receipt_validation_mode(self.env), "both")

    def test_receipt_modes_roundtrip(self):
        icp = self.env["ir.config_parameter"].sudo()
        for mode in ("both", "warehouse_only", "buyer_only"):
            icp.set_param(ICP_RECEIPT_MODE, mode)
            self.assertEqual(receipt_validation_mode(self.env), mode)

    def test_role_group_xmlid_exists(self):
        group = self.env.ref(
            "almohit_core.group_almohit_warehouse_manager",
            raise_if_not_found=False,
        )
        self.assertTrue(group)

    def test_models_exist(self):
        for model in (
            "almohit.warehouse.assignment",
            "almohit.warehouse.transfer",
            "almohit.warehouse.discrepancy",
            "almohit.inventory.session",
        ):
            self.assertIn(model, self.env)

    def test_picking_dispatch_fields(self):
        picking = self.env["stock.picking"]
        self.assertIn("almohit_dispatch_state", picking._fields)
        self.assertIn("almohit_dispatch_picking_id", picking._fields)
        self.assertIn("almohit_storefront_delivery", picking._fields)

    def test_xlsx_parser_reads_headers(self):
        # Build a tiny XLSX with shared strings via zip/xml
        sheet = """<?xml version="1.0"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData>
            <row r="1">
              <c r="A1" t="inlineStr"><is><t>default_code</t></is></c>
              <c r="B1" t="inlineStr"><is><t>quantity</t></is></c>
            </row>
            <row r="2">
              <c r="A2" t="inlineStr"><is><t>SKU-1</t></is></c>
              <c r="B2"><v>3</v></c>
            </row>
          </sheetData>
        </worksheet>"""
        content_types = """<?xml version="1.0"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/xl/worksheets/sheet1.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        </Types>"""
        rels = """<?xml version="1.0"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
            Target="xl/workbook.xml"/>
        </Relationships>"""
        workbook = """<?xml version="1.0"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
        </workbook>"""
        wb_rels = """<?xml version="1.0"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
            Target="worksheets/sheet1.xml"/>
        </Relationships>"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        rows = warehouse_inventory._parse_xlsx_rows(buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1].get("default_code"), "SKU-1")
        self.assertEqual(rows[0][1].get("quantity"), "3")

    def test_csv_parser(self):
        text = "default_code,quantity\nABC,2\n"
        rows = warehouse_inventory._parse_csv_rows(text.encode("utf-8"))
        self.assertEqual(rows[0][1]["default_code"], "ABC")
        self.assertEqual(rows[0][1]["quantity"], "2")

    def test_transfer_action_helpers_exist(self):
        self.assertTrue(callable(warehouse_transfers.cancel_transfer))
        self.assertTrue(callable(warehouse_transfers.reject_transfer))
        self.assertTrue(callable(warehouse_transfers.close_transfer))
        self.assertTrue(callable(warehouse_transfers.resolve_discrepancy))
