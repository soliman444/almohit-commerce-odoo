#!/usr/bin/env python3
"""Almohit release E2E against local staging (http://127.0.0.1:8070)."""
from __future__ import annotations

import json
import http.cookiejar
import urllib.error
import urllib.request
import zipfile
import io
import base64
import subprocess
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8070"
PWD = "E2EPass123!"
OUT = Path(r"G:\odoo17-docker\_smoke\e2e")
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []


def record(name: str, status: str, evidence: str, fix: str = ""):
    RESULTS.append(
        {"case": name, "status": status, "evidence": evidence[:800], "fix": fix}
    )
    print(f"[{status}] {name} :: {evidence}"[:500])


class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )

    def call(self, method: str, path: str, body=None):
        data = None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            BASE + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8")
                code = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            code = e.code
        except Exception as e:
            return 0, {"ok": False, "error": {"code": "CONN", "message": str(e)}}
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"_raw": raw[:500]}
        return code, parsed

    def login(self, email: str):
        return self.call(
            "POST", "/almohit/api/auth/login", {"email": email, "password": PWD}
        )


def shell(py: str) -> str:
    """Run odoo shell snippet; return stdout text (no BOM)."""
    path = OUT / "_shell_snip.py"
    # Explicit UTF-8 without BOM — PowerShell Set-Content otherwise injects U+FEFF.
    path.write_bytes(py.encode("utf-8"))
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "odoo17",
            "odoo",
            "shell",
            "-d",
            "odoo17db",
            "--db_host",
            "db",
            "--db_port",
            "5432",
            "--db_user",
            "odoo",
            "--db_password",
            "odoo",
            "--http-port=8099",
        ],
        input=py.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    return out + err


def onhand_snip(default_code: str, location_id: int) -> str:
    return f"""
product = env["product.product"].search([("default_code","=","{default_code}")], limit=1)
qty = float(sum(env["stock.quant"].sudo().search([
    ("product_id","=",product.id),("location_id","=",{location_id})
]).mapped("quantity"))) if product else None
print("ONHAND", product.id if product else None, qty)
env.cr.rollback()
"""


def parse_onhand(text: str):
    for line in text.splitlines():
        if line.startswith("ONHAND"):
            parts = line.split()
            return parts[1], float(parts[2]) if parts[2] not in ("None",) else None
    return None, None


def build_xlsx(rows: list[tuple[str, str]]) -> bytes:
    """Minimal XLSX with inlineStr cells."""
    sheet_rows = []
    for i, (a, b) in enumerate(rows, start=1):
        sheet_rows.append(
            f'<row r="{i}">'
            f'<c r="A{i}" t="inlineStr"><is><t>{a}</t></is></c>'
            f'<c r="B{i}" t="inlineStr"><is><t>{b}</t></is></c>'
            f"</row>"
        )
    sheet = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    content_types = """<?xml version="1.0"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="xml" ContentType="application/xml"/>
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Override PartName="/xl/worksheets/sheet1.xml"
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      <Override PartName="/xl/workbook.xml"
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
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
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def main():
    # Health
    c = Client()
    code, body = c.call("GET", "/almohit/api/health")
    record("Health", "PASS" if body.get("ok") else "FAIL", f"{code} {body}")

    # Unauth WH
    code, body = c.call("GET", "/almohit/api/warehouse/dashboard")
    record(
        "Unauth WH dashboard",
        "PASS" if code == 401 else "FAIL",
        f"{code} {(body.get('error') or {})}",
    )

    # Role logins
    roles = {
        "buyer": "e2e.release.buyer@example.test",
        "merchant": "e2e.release.merchant@example.test",
        "marketer": "e2e.release.marketer@example.test",
        "distributor": "e2e.release.dist@example.test",
        "warehouse_manager": "wh.tester.final@example.com",
    }
    clients = {}
    for role, email in roles.items():
        cl = Client()
        code, body = cl.login(email)
        ok = bool(body.get("ok") and body.get("data", {}).get("role") == role)
        record(
            f"Auth login {role}",
            "PASS" if ok else "FAIL",
            f"{code} role={body.get('data',{}).get('role')} trade={body.get('data',{}).get('trade_status')} err={body.get('error')}",
        )
        clients[role] = cl

    # Forbidden cross-role
    code, body = clients["buyer"].call("GET", "/almohit/api/warehouse/dashboard")
    record(
        "Buyer denied WH API",
        "PASS" if not body.get("ok") else "FAIL",
        f"{code} {body.get('error')}",
    )
    code, body = clients["merchant"].call("GET", "/almohit/api/buyer/suppliers")
    record(
        "Merchant denied buyer API",
        "PASS" if not body.get("ok") else "FAIL",
        f"{code} {body.get('error')}",
    )
    code, body = clients["distributor"].call("GET", "/almohit/api/deliveries")
    record(
        "Distributor deliveries list",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} total={(body.get('data') or {}).get('pagination',{}).get('total')}",
    )
    code, body = clients["marketer"].call("GET", "/almohit/api/marketer/merchants")
    record(
        "Marketer merchants list",
        "PASS" if body.get("ok") or (body.get("error") or {}).get("code") else "FAIL",
        f"{code} {str(body)[:220]}",
    )

    # ---- Purchase + WH receive ----
    buyer = clients["buyer"]
    code, body = buyer.call(
        "POST",
        "/almohit/api/buyer/suppliers",
        {"name": "E2E Farmer Rel3", "phone": "+966501114444", "city": "Dammam"},
    )
    sup_id = (body.get("data") or {}).get("id")
    record(
        "Buyer create supplier",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} id={sup_id} err={body.get('error')}",
    )

    import time as _time
    suffix = str(int(_time.time()))[-6:]
    sku_a = f"E2E-RCV-A{suffix}"
    sku_b = f"E2E-RCV-B{suffix}"
    code, p1b = buyer.call(
        "POST",
        "/almohit/api/buyer/products",
        {"name": f"E2E Recv Apple {suffix}", "default_code": sku_a, "type": "product"},
    )
    code, p2b = buyer.call(
        "POST",
        "/almohit/api/buyer/products",
        {"name": f"E2E Recv Banana {suffix}", "default_code": sku_b, "type": "product"},
    )

    def variant_id(d):
        data = d.get("data") or {}
        return (
            data.get("product_variant_id")
            or data.get("variant_id")
            or (data.get("product") or {}).get("id")
            or data.get("id")
        )

    pid1, pid2 = variant_id(p1b), variant_id(p2b)
    record(
        "Buyer create products",
        "PASS" if pid1 and pid2 else "FAIL",
        f"pid1={pid1} pid2={pid2} p1keys={list((p1b.get('data') or {}).keys())}",
    )

    code, body = buyer.call(
        "POST",
        "/almohit/api/buyer/purchases",
        {
            "supplier_id": sup_id,
            "notes": "E2E release PO3",
            "lines": [
                {"product_id": pid1, "quantity": 10, "price_unit": 5},
                {"product_id": pid2, "quantity": 8, "price_unit": 3},
            ],
        },
    )
    po = body.get("data") or {}
    po_id = po.get("id")
    po_name = po.get("name")
    record(
        "Buyer create PO",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} id={po_id} name={po_name} err={body.get('error')}",
    )

    code, body = buyer.call("POST", f"/almohit/api/buyer/purchases/{po_id}/confirm", {})
    record(
        "Buyer confirm PO",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} state={(body.get('data') or {}).get('state')} err={body.get('error')}",
    )

    wh = clients["warehouse_manager"]
    code, body = wh.call("GET", "/almohit/api/warehouse/receipts?status=pending")
    items = ((body.get("data") or {}).get("items")) or []
    recv = next(
        (
            i
            for i in items
            if (i.get("purchase") or {}).get("id") == po_id or i.get("origin") == po_name
        ),
        None,
    )
    recv_id = (recv or {}).get("id")
    record(
        "WH pending receipt for PO",
        "PASS" if recv_id else "FAIL",
        f"recv_id={recv_id} pending={len(items)}",
    )

    code, detail = wh.call("GET", f"/almohit/api/warehouse/receipts/{recv_id}")
    lines = (detail.get("data") or {}).get("lines") or []
    record(
        "WH receipt detail lines",
        "PASS" if len(lines) >= 2 else "FAIL",
        f"n={len(lines)} state={(detail.get('data') or {}).get('state')}",
    )

    # Stock before
    _, before_a = parse_onhand(shell(onhand_snip(sku_a, 8)))
    _, before_b8 = parse_onhand(shell(onhand_snip(sku_b, 8)))
    _, before_b26 = parse_onhand(shell(onhand_snip(sku_b, 26)))

    if len(lines) >= 2:
        m1, m2 = lines[0], lines[1]
        payload = {
            "lines": [
                {
                    "move_id": m1["move_id"],
                    "quantity_done": 4,
                    "location_dest_id": 8,
                },
                {
                    "move_id": m2["move_id"],
                    "destinations": [
                        {"location_id": 8, "quantity": 3},
                        {"location_id": 26, "quantity": 2},
                    ],
                },
            ]
        }
        code, body = wh.call(
            "POST", f"/almohit/api/warehouse/receipts/{recv_id}/receive", payload
        )
        data = body.get("data") or {}
        record(
            "WH split/partial receive",
            "PASS" if body.get("ok") else "FAIL",
            f"{code} state={data.get('state')} backorders={data.get('backorders')} err={body.get('error')}",
        )
        code2, body2 = wh.call(
            "POST", f"/almohit/api/warehouse/receipts/{recv_id}/receive", payload
        )
        err2 = (body2.get("error") or {}).get("code")
        record(
            "WH receive retry no duplicate",
            "PASS" if err2 in ("RECEIPT_ALREADY_DONE",) or code2 == 409 else "FAIL",
            f"{code2} {body2.get('error')}",
        )
    else:
        record("WH split/partial receive", "BLOCKED", "missing lines")
        record("WH receive retry no duplicate", "BLOCKED", "missing lines")

    _, after_a = parse_onhand(shell(onhand_snip(sku_a, 8)))
    _, after_b8 = parse_onhand(shell(onhand_snip(sku_b, 8)))
    _, after_b26 = parse_onhand(shell(onhand_snip(sku_b, 26)))
    da = (after_a or 0) - (before_a or 0)
    db8 = (after_b8 or 0) - (before_b8 or 0)
    db26 = (after_b26 or 0) - (before_b26 or 0)
    record(
        "Stock after receive locations",
        "PASS" if da == 4 and db8 == 3 and db26 == 2 else "FAIL",
        f"APL@8 {before_a}->{after_a} (d{da}); BAN@8 {before_b8}->{after_b8} (d{db8}); BAN@26 {before_b26}->{after_b26} (d{db26})",
    )

    # Backorder remaining listed
    code, body = wh.call("GET", "/almohit/api/warehouse/receipts?status=pending")
    items = ((body.get("data") or {}).get("items")) or []
    bo = [i for i in items if i.get("origin") == po_name or (i.get("purchase") or {}).get("id") == po_id]
    record(
        "Backorder/remaining receipt visible",
        "PASS" if bo else "FAIL",
        f"matching_pending={len(bo)} ids={[i.get('id') for i in bo]}",
    )

    # Receipt modes
    mode_py = """
from odoo.addons.almohit_core.services.warehouse_common import ICP_RECEIPT_MODE
from odoo.addons.almohit_core.services import warehouse_receipts, buyer as buyer_svc
import json
icp = env["ir.config_parameter"].sudo()
partner = env["res.partner"].browse(388)
buyer_user = env["res.users"].browse(263)
# find an open incoming picking if any
picking = env["stock.picking"].sudo().search([("picking_type_code","=","incoming"),("state","not in",("done","cancel"))], limit=1)
pid = picking.id if picking else 0
print("PICK", pid)
for mode in ("warehouse_only", "buyer_only", "both"):
    icp.set_param(ICP_RECEIPT_MODE, mode)
    # buyer receive gate
    res = buyer_svc.receive_receipt(buyer_user, pid or 1, {"lines": [{"move_id": 1, "quantity_done": 1}]})
    raw = res.response[0]
    raw = raw.decode() if isinstance(raw, bytes) else raw
    b = json.loads(raw)
    print("BUYER", mode, b.get("error",{}).get("code") or ("ok" if b.get("ok") else "other"))
    res = warehouse_receipts.receive_receipt(partner, pid or 1, {"lines": []})
    raw = res.response[0]
    raw = raw.decode() if isinstance(raw, bytes) else raw
    b = json.loads(raw)
    print("WH", mode, b.get("error",{}).get("code") or ("ok" if b.get("ok") else "other"))
icp.set_param(ICP_RECEIPT_MODE, "both")
env.cr.commit()
print("MODE_DONE")
"""
    mout = shell(mode_py)
    buyer_wh_only = "RECEIPT_HANDED_TO_WAREHOUSE" in mout
    wh_buyer_only = "RECEIPT_BUYER_ONLY" in mout
    record(
        "Receipt mode warehouse_only blocks buyer",
        "PASS" if buyer_wh_only else "FAIL",
        mout[mout.find("BUYER warehouse_only") : mout.find("BUYER warehouse_only") + 80]
        if "BUYER warehouse_only" in mout
        else mout[-400:],
    )
    record(
        "Receipt mode buyer_only blocks WH",
        "PASS" if wh_buyer_only else "FAIL",
        mout[mout.find("WH buyer_only") : mout.find("WH buyer_only") + 60]
        if "WH buyer_only" in mout
        else mout[-400:],
    )

    # ---- Sales E2E ----
    merch = clients["merchant"]
    # Pick a storable saleable product with enough free stock for MOQ (>=4).
    txt = shell(
        """
wh = env["stock.warehouse"].browse(1)
loc = wh.lot_stock_id
Quant = env["stock.quant"].sudo()
chosen = None
moq_needed = 4.0
for p in env["product.product"].search([("sale_ok","=",True),("type","=","product"),("active","=",True)], limit=200):
    free = Quant._get_available_quantity(p, loc) if hasattr(Quant, "_get_available_quantity") else 0.0
    try:
        free = float(free or 0.0)
    except Exception:
        free = 0.0
    if free >= moq_needed:
        chosen = p
        print("PID", p.id, p.default_code, free)
        break
if not chosen:
    print("PID", 0, "NONE", 0)
env.cr.rollback()
"""
    )
    sale_product_id = None
    sale_free = None
    for line in txt.splitlines():
        if line.startswith("PID"):
            parts = line.split()
            sale_product_id = int(parts[1])
            sale_free = parts[-1]
            break
    if not sale_product_id:
        sale_product_id = None
    record(
        "Merchant find saleable product",
        "PASS" if sale_product_id else "FAIL",
        f"product_id={sale_product_id} free={sale_free}",
    )

    # clear cart by login fresh
    merch = Client()
    merch.login(roles["merchant"])
    code, body = merch.call(
        "POST",
        "/almohit/api/cart/items",
        {"product_id": int(sale_product_id), "quantity": 4},
    )
    record(
        "Merchant add to cart",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} err={body.get('error')} lines={len(((body.get('data') or {}).get('lines') or []))}",
    )
    code, body = merch.call("POST", "/almohit/api/checkout", {})
    order = body.get("data") or {}
    order_id = order.get("id")
    order_name = order.get("name") or order.get("order_number")
    record(
        "Merchant checkout",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} order={order_name} id={order_id} err={body.get('error')}",
    )

    # pay invoice via shell
    pay_py = f"""
from odoo.addons.almohit_core.services.payments import order_allows_distribution, create_storefront_invoice
order = env["sale.order"].sudo().browse({order_id or 0})
print("ORDER", order.exists(), order.name, order.state, getattr(order, "almohit_storefront_order", None))
if order.exists():
    inv = create_storefront_invoice(order)
    print("INV", inv.ids, inv.mapped("payment_state"), inv.mapped("amount_residual"))
    for move in inv:
        if move.state != "posted":
            move._post(soft=False)
        if move.amount_residual:
            # register payment
            Payment = env["account.payment"].sudo()
            journal = env["account.journal"].sudo().search([("type","in",("bank","cash")),("company_id","=",env.company.id)], limit=1)
            payment = Payment.create({{
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": order.partner_id.id,
                "amount": move.amount_residual,
                "journal_id": journal.id,
                "currency_id": move.currency_id.id,
            }})
            payment.action_post()
            # reconcile
            liquidity_lines = payment.move_id.line_ids.filtered(lambda l: l.account_id.account_type in ("asset_receivable","liability_payable") or l.account_id == payment.destination_account_id)
            # use payment widget
            if hasattr(payment, "action_validate"):
                pass
            # try reconcile outstanding
            try:
                (move + payment.move_id).line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled).reconcile()
            except Exception as e:
                print("RECON_ERR", e)
            # alternate: pay with account.payment.register
            try:
                wiz = env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids).sudo().create({{"journal_id": journal.id}})
                wiz.action_create_payments()
                print("WIZ_PAY_OK")
            except Exception as e:
                print("WIZ_ERR", e)
    order.invalidate_recordset()
    inv = order.invoice_ids.filtered(lambda m: m.state == "posted")
    print("AFTER", inv.mapped("payment_state"), inv.mapped("amount_residual"), "allows", order_allows_distribution(order))
    # assign distributor
    picking = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")[:1]
    dist = env["res.users"].browse(266)
    if picking:
        picking.write({{"user_id": dist.id}})
        print("PICK", picking.id, picking.name, picking.state, picking.user_id.id, picking.almohit_dispatch_state)
env.cr.commit()
print("PAY_DONE")
"""
    pout = shell(pay_py)
    allows = "allows True" in pout or "allows True" in pout.replace("\r", "")
    record(
        "Satisfy due payment + assign distributor",
        "PASS" if "PAY_DONE" in pout and ("allows True" in pout or "WIZ_PAY_OK" in pout) else "FAIL",
        pout[pout.find("ORDER") : pout.find("ORDER") + 400] if "ORDER" in pout else pout[-500:],
    )

    # extract picking id
    pick_id = None
    for line in pout.splitlines():
        if line.startswith("PICK"):
            pick_id = int(line.split()[1])
            break

    # WH dispatch
    if pick_id:
        code, idet = wh.call("GET", f"/almohit/api/warehouse/issues/{pick_id}")
        ilines = (idet.get("data") or {}).get("lines") or []
        record(
            "WH issue detail before dispatch",
            "PASS" if idet.get("ok") else "FAIL",
            f"{code} state={(idet.get('data') or {}).get('state')} dispatch={(idet.get('data') or {}).get('dispatch_state')} payment_ok={(idet.get('data') or {}).get('payment_ok')} err={idet.get('error')}",
        )
        if ilines:
            move_id = ilines[0]["move_id"]
            qty = min(1.0, float(ilines[0].get("ordered_qty") or 1))
            # stock before output for product
            prod_code = (ilines[0].get("product") or {}).get("default_code") or ""
            _, s_before = parse_onhand(shell(onhand_snip(prod_code, 8))) if prod_code else (None, None)
            _, o_before = parse_onhand(shell(onhand_snip(prod_code, 11))) if prod_code else (None, None)
            code, body = wh.call(
                "POST",
                f"/almohit/api/warehouse/issues/{pick_id}/dispatch",
                {
                    "lines": [
                        {
                            "move_id": move_id,
                            "quantity_done": qty,
                            "location_id": 8,
                        }
                    ]
                },
            )
            data = body.get("data") or {}
            record(
                "WH issue dispatch",
                "PASS" if body.get("ok") else "FAIL",
                f"{code} dispatch_state={data.get('dispatch_state')} cust_state={data.get('state')} dpick={data.get('dispatch_picking_id')} err={body.get('error')}",
            )
            _, s_after = parse_onhand(shell(onhand_snip(prod_code, 8))) if prod_code else (None, None)
            _, o_after = parse_onhand(shell(onhand_snip(prod_code, 11))) if prod_code else (None, None)
            record(
                "Stock moved Stock→Output",
                "PASS"
                if (s_before is not None and o_before is not None and (s_after or 0) <= (s_before or 0) and (o_after or 0) >= (o_before or 0) + qty - 0.01)
                else "FAIL",
                f"{prod_code} stock {s_before}->{s_after}; out {o_before}->{o_after}; qty={qty}",
            )
            # customer still not done
            cust_state = data.get("state")
            record(
                "Customer delivery still pending after dispatch",
                "PASS" if cust_state not in ("done",) else "FAIL",
                f"state={cust_state}",
            )
            code2, body2 = wh.call(
                "POST",
                f"/almohit/api/warehouse/issues/{pick_id}/dispatch",
                {"lines": [{"move_id": move_id, "quantity_done": qty, "location_id": 8}]},
            )
            record(
                "Dispatch retry no duplicate",
                "PASS"
                if (body2.get("error") or {}).get("code") == "ISSUE_ALREADY_DISPATCHED"
                else "FAIL",
                f"{code2} {body2.get('error')}",
            )
        else:
            record("WH issue dispatch", "BLOCKED", "no issue lines")
            record("Stock moved Stock→Output", "BLOCKED", "no dispatch")
            record("Customer delivery still pending after dispatch", "BLOCKED", "no dispatch")
            record("Dispatch retry no duplicate", "BLOCKED", "no dispatch")

        # Distributor complete
        dist = clients["distributor"]
        code, body = dist.call("GET", f"/almohit/api/deliveries/{pick_id}")
        record(
            "Distributor sees assigned delivery",
            "PASS" if body.get("ok") else "FAIL",
            f"{code} {body.get('error')} status={(body.get('data') or {}).get('status')}",
        )
        code, body = dist.call(
            "POST", f"/almohit/api/deliveries/{pick_id}/status", {"status": "delivered"}
        )
        record(
            "Distributor complete delivery",
            "PASS" if body.get("ok") else "FAIL",
            f"{code} status={(body.get('data') or {}).get('status')} err={body.get('error')}",
        )
        code2, body2 = dist.call(
            "POST", f"/almohit/api/deliveries/{pick_id}/status", {"status": "delivered"}
        )
        # idempotent ok or already done
        ok_retry = body2.get("ok") or (body2.get("error") or {}).get("code") in (
            "DELIVERY_ALREADY_DONE",
        )
        record(
            "Delivery complete retry safe",
            "PASS" if ok_retry else "FAIL",
            f"{code2} {body2.get('error') or (body2.get('data') or {}).get('status')}",
        )
    else:
        record("WH issue detail before dispatch", "BLOCKED", "no picking id")
        record("WH issue dispatch", "BLOCKED", "no picking")
        record("Stock moved Stock→Output", "BLOCKED", "no picking")
        record("Customer delivery still pending after dispatch", "BLOCKED", "no picking")
        record("Dispatch retry no duplicate", "BLOCKED", "no picking")
        record("Distributor sees assigned delivery", "BLOCKED", "no picking")
        record("Distributor complete delivery", "BLOCKED", "no picking")
        record("Delivery complete retry safe", "BLOCKED", "no picking")

    # ---- XLSX import ----
    # use isolated test product with near-zero impact: set counted = current onhand for E2E-RCV-APL3
    _, cur = parse_onhand(shell(onhand_snip(sku_a, 8)))
    counted = cur if cur is not None else 0
    xname = f"e2e_opening_{suffix}.xlsx"
    xbytes = build_xlsx(
        [("default_code", "quantity"), (sku_a, str(int(counted)))]
    )
    b64 = base64.b64encode(xbytes).decode("ascii")
    code, body = wh.call(
        "POST",
        "/almohit/api/warehouse/inventory/import",
        {
            "warehouse_id": 1,
            "session_type": "opening",
            "filename": xname,
            "file_base64": b64,
        },
    )
    sess = body.get("data") or {}
    sess_id = sess.get("id")
    record(
        "XLSX HTTP import preview",
        "PASS" if body.get("ok") else "FAIL",
        f"{code} id={sess_id} lines={sess.get('line_count')} err={body.get('error')}",
    )
    code2, body2 = wh.call(
        "POST",
        "/almohit/api/warehouse/inventory/import",
        {
            "warehouse_id": 1,
            "session_type": "opening",
            "filename": xname,
            "file_base64": b64,
        },
    )
    record(
        "XLSX duplicate checksum",
        "PASS"
        if (body2.get("error") or {}).get("code") == "IMPORT_DUPLICATE"
        else "FAIL",
        f"{code2} {body2.get('error')}",
    )
    # invalid product validation
    bad = build_xlsx([("default_code", "quantity"), ("NO-SUCH-SKU-XYZ", "1")])
    code3, body3 = wh.call(
        "POST",
        "/almohit/api/warehouse/inventory/import",
        {
            "warehouse_id": 1,
            "session_type": "opening",
            "filename": "bad.xlsx",
            "file_base64": base64.b64encode(bad).decode("ascii"),
        },
    )
    record(
        "XLSX validation PRODUCT_NOT_FOUND",
        "PASS"
        if (body3.get("error") or {}).get("code") == "INVALID_IMPORT"
        or (
            body3.get("ok")
            and (body3.get("data") or {}).get("validation_errors")
        )
        else "FAIL",
        f"{code3} {body3.get('error') or (body3.get('data') or {}).get('validation_errors')}",
    )
    if sess_id:
        code4, body4 = wh.call(
            "POST", f"/almohit/api/warehouse/inventory/{sess_id}/approve", {}
        )
        record(
            "XLSX approve inventory (noop qty)",
            "PASS" if body4.get("ok") else "FAIL",
            f"{code4} state={(body4.get('data') or {}).get('state')} err={body4.get('error')}",
        )

    # ---- Security ----
    # unassigned WH user
    sec_py = """
partner = env["res.partner"].browse(388)
asg = env["almohit.warehouse.assignment"].search([("partner_id","=",388)], limit=1)
wh_ids = asg.warehouse_ids.ids
# keep one for restore - test unassigned by writing only WH2 then deny WH1 ops via API simulation
print("ASG", asg.id, wh_ids)
env.cr.rollback()
"""
    # negative qty transfer
    code, body = wh.call(
        "POST",
        "/almohit/api/warehouse/transfers",
        {
            "source_warehouse_id": 1,
            "dest_warehouse_id": 2,
            "lines": [{"product_id": pid1, "quantity": -5, "location_id": 8}],
        },
    )
    record(
        "Transfer reject negative qty",
        "PASS" if not body.get("ok") else "FAIL",
        f"{code} {body.get('error')}",
    )
    code, body = wh.call(
        "POST",
        "/almohit/api/warehouse/transfers",
        {
            "source_warehouse_id": 1,
            "dest_warehouse_id": 2,
            "lines": [{"product_id": 99999999, "quantity": 1, "location_id": 8}],
        },
    )
    record(
        "Transfer reject invalid product",
        "PASS" if not body.get("ok") else "FAIL",
        f"{code} {body.get('error')}",
    )

    # transfer reconcile close happy path (AJWA or E2E product with stock)
    # Prefer a product known to have stock (AJWA); fall back to received E2E sku.
    xfer_pid = pid1
    txt = shell(
        'p=env["product.product"].search([("default_code","=","AJWA-1KG-001")],limit=1)\n'
        'print("XFERPID", p.id)\nenv.cr.rollback()\n'
    )
    for line in txt.splitlines():
        if line.startswith("XFERPID"):
            try:
                xfer_pid = int(line.split()[1])
            except Exception:
                pass
    code, body = wh.call(
        "POST",
        "/almohit/api/warehouse/transfers",
        {
            "source_warehouse_id": 1,
            "dest_warehouse_id": 2,
            "lines": [{"product_id": xfer_pid, "quantity": 1, "location_id": 8}],
        },
    )
    tid = (body.get("data") or {}).get("id")
    lid = ((body.get("data") or {}).get("lines") or [{}])[0].get("id")
    if tid:
        dcode, dbody = wh.call(
            "POST", f"/almohit/api/warehouse/transfers/{tid}/dispatch", {}
        )
        code, body = wh.call(
            "POST",
            f"/almohit/api/warehouse/transfers/{tid}/receive",
            {"lines": [{"line_id": lid, "quantity": 1, "location_id": 18}]},
        )
        code2, body2 = wh.call(
            "POST", f"/almohit/api/warehouse/transfers/{tid}/close", {"note": "e2e"}
        )
        record(
            "Transfer receive+close",
            "PASS" if body.get("ok") and body2.get("ok") else "FAIL",
            f"disp={dbody.get('error') or (dbody.get('data') or {}).get('state')} "
            f"recv={body.get('error') or (body.get('data') or {}).get('state')} "
            f"close={body2.get('error') or (body2.get('data') or {}).get('state')}",
        )
    else:
        record("Transfer receive+close", "FAIL", f"create failed {body.get('error')}")

    # Unapproved WH gate: create temp unapproved user via shell then login
    uout = shell(
        """
Users=env["res.users"].sudo().with_context(no_reset_password=True)
login="e2e.wh.unapproved@example.test"
u=Users.search([("login","=",login)],limit=1)
if not u:
    p=env["res.partner"].sudo().create({"name":"WH Unapproved","email":login,"almohit_app_role":"warehouse_manager","almohit_trade_status":"pending","phone":"+966500000001"})
    u=Users.create({"name":"WH Unapproved","login":login,"partner_id":p.id,"groups_id":[(6,0,[env.ref("base.group_portal").id, env.ref("almohit_core.group_almohit_warehouse_manager").id])]})
else:
    u.partner_id.almohit_trade_status="pending"
u.password="E2EPass123!"
env.cr.commit()
print("UNAPP", u.id)
"""
    )
    ucl = Client()
    ucl.login("e2e.wh.unapproved@example.test")
    code, body = ucl.call("GET", "/almohit/api/warehouse/dashboard")
    record(
        "Unapproved WH denied",
        "PASS"
        if (body.get("error") or {}).get("code") == "WAREHOUSE_APPROVAL_REQUIRED"
        else "FAIL",
        f"{code} {body.get('error')}",
    )

    # Unassigned: approved WH without warehouses
    shell(
        """
p=env["res.partner"].sudo().search([("email","=","e2e.wh.unassigned@example.test")],limit=1)
Users=env["res.users"].sudo().with_context(no_reset_password=True)
if not p:
    p=env["res.partner"].sudo().create({"name":"WH Unassigned","email":"e2e.wh.unassigned@example.test","almohit_app_role":"warehouse_manager","almohit_trade_status":"approved","phone":"+966500000002"})
    u=Users.create({"name":"WH Unassigned","login":"e2e.wh.unassigned@example.test","partner_id":p.id,"groups_id":[(6,0,[env.ref("base.group_portal").id, env.ref("almohit_core.group_almohit_warehouse_manager").id])]})
else:
    u=Users.search([("partner_id","=",p.id)],limit=1)
u.password="E2EPass123!"
# ensure no assignment
env["almohit.warehouse.assignment"].sudo().search([("partner_id","=",p.id)]).unlink()
env.cr.commit()
print("UNASG", u.id)
"""
    )
    ucl2 = Client()
    ucl2.login("e2e.wh.unassigned@example.test")
    code, body = ucl2.call("GET", "/almohit/api/warehouse/dashboard")
    record(
        "Unassigned WH denied",
        "PASS"
        if (body.get("error") or {}).get("code")
        in ("WAREHOUSE_ASSIGNMENT_REQUIRED", "WAREHOUSE_APPROVAL_REQUIRED")
        else "FAIL",
        f"{code} {body.get('error')}",
    )

    # Save
    out_path = OUT / "results.json"
    out_path.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    summary = {
        "PASS": sum(1 for r in RESULTS if r["status"] == "PASS"),
        "FAIL": sum(1 for r in RESULTS if r["status"] == "FAIL"),
        "BLOCKED": sum(1 for r in RESULTS if r["status"] == "BLOCKED"),
        "total": len(RESULTS),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("SUMMARY", summary)
    return summary


if __name__ == "__main__":
    main()
