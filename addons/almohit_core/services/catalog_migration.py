from odoo import SUPERUSER_ID, Command
import hmac

from .legacy_xmlid import bind_xmlid, browse_xmlid, xmlid


WHOLESALE_XMLID = "almohit_core.pricelist_wholesale"


def migration_env(request):
    """Allow a system admin session or the one-time migration token."""
    token = request.httprequest.headers.get("X-Almohit-Migration-Token") or ""
    stored = request.env["ir.config_parameter"].sudo().get_param(
        "almohit.migration_token",
    ) or ""
    user = request.env.user
    if stored and token and hmac.compare_digest(token.encode("utf-8"), stored.encode("utf-8")):
        return request.env(user=SUPERUSER_ID, su=True)
    if user and not user._is_public() and user.has_group("base.group_system"):
        return request.env
    return None


def _result(action, xml_id, odoo_id=None, extra=None):
    row = {"action": action, "xmlid": xml_id, "odoo_id": odoo_id}
    if extra:
        row.update(extra)
    return row


def upsert_category(env, record):
    xml_id = xmlid("category", record["legacy_id"])
    vals = {
        "name": record["name"],
        "parent_id": False,
    }
    if record.get("parent_legacy_id"):
        parent = browse_xmlid(env, "category", record["parent_legacy_id"])
        if parent:
            vals["parent_id"] = parent.id
    existing = browse_xmlid(env, "category", record["legacy_id"])
    if existing:
        existing.write(vals)
        return _result("updated", xml_id, existing.id)
    created = env["product.category"].create(vals)
    bind_xmlid(env, "category", record["legacy_id"], created)
    return _result("created", xml_id, created.id)


def upsert_brand(env, record):
    xml_id = xmlid("brand", record["legacy_id"])
    vals = {
        "name": record["name"],
        "active": bool(record.get("active", True)),
    }
    if record.get("image_base64"):
        vals["image_1920"] = record["image_base64"]
    existing = browse_xmlid(env, "brand", record["legacy_id"])
    if existing:
        existing.write(vals)
        return _result("updated", xml_id, existing.id)
    created = env["almohit.product.brand"].create(vals)
    bind_xmlid(env, "brand", record["legacy_id"], created)
    return _result("created", xml_id, created.id)


def upsert_attribute(env, record):
    xml_id = xmlid("attribute", record["legacy_id"])
    display_type = "color" if record.get("type") == "COLOR" else "select"
    vals = {
        "name": record["name"],
        "create_variant": "always",
        "display_type": display_type,
        "sequence": int(record.get("sequence") or 10),
    }
    existing = browse_xmlid(env, "attribute", record["legacy_id"])
    if existing:
        existing.write({
            "name": vals["name"],
            "sequence": vals["sequence"],
        })
        attr = existing
        action = "updated"
    else:
        attr = env["product.attribute"].create(vals)
        bind_xmlid(env, "attribute", record["legacy_id"], attr)
        action = "created"
    value_results = []
    for value in record.get("values") or []:
        value_results.append(_upsert_attribute_value(env, attr, value))
    return _result(action, xml_id, attr.id, extra={"values": value_results})


def _upsert_attribute_value(env, attribute, record):
    xml_id = xmlid("attribute_value", record["legacy_id"])
    vals = {
        "name": record["name"],
        "attribute_id": attribute.id,
        "sequence": int(record.get("sequence") or 10),
    }
    if record.get("html_color"):
        vals["html_color"] = record["html_color"]
    existing = browse_xmlid(env, "attribute_value", record["legacy_id"])
    if existing:
        existing.write(vals)
        return _result("updated", xml_id, existing.id)
    created = env["product.attribute.value"].create(vals)
    bind_xmlid(env, "attribute_value", record["legacy_id"], created)
    return _result("created", xml_id, created.id)


def _sku_conflict(env, sku, mapped):
    if not sku:
        return None
    other = env["product.product"].sudo().search(
        [("default_code", "=", sku)],
        limit=1,
    )
    if not other:
        return None
    if mapped and other.id == mapped.id:
        return None
    if mapped and other.product_tmpl_id.id == mapped.id:
        return None
    ours = env["ir.model.data"].sudo().search([
        ("module", "=", "almohit_legacy"),
        ("model", "in", ["product.product", "product.template"]),
        ("res_id", "in", [other.id, other.product_tmpl_id.id]),
    ], limit=1)
    if ours:
        return None
    return other


def upsert_product(env, record):
    xml_id = xmlid("product", record["legacy_id"])
    existing = browse_xmlid(env, "product", record["legacy_id"])
    conflict = _sku_conflict(env, record.get("default_code"), existing)
    if conflict:
        return _result(
            "skipped",
            xml_id,
            extra={
                "reason": "SKU_EXISTS_UNMAPPED",
                "sku": record.get("default_code"),
                "existing_id": conflict.id,
            },
        )
    categ = False
    if record.get("category_legacy_id"):
        categ = browse_xmlid(env, "category", record["category_legacy_id"])
    brand = False
    if record.get("brand_legacy_id"):
        brand = browse_xmlid(env, "brand", record["brand_legacy_id"])
    unit = env.ref("uom.product_uom_unit", raise_if_not_found=False)
    vals = {
        "name": record["name"],
        "default_code": record.get("default_code") or False,
        "description_sale": record.get("description_sale") or False,
        "description": record.get("description") or False,
        "categ_id": categ.id if categ else env.ref("product.product_category_all").id,
        "almohit_brand_id": brand.id if brand else False,
        "almohit_min_order_qty": float(record.get("min_order_qty") or 1.0),
        "sale_ok": bool(record.get("sale_ok", True)),
        "active": bool(record.get("active", True)),
        "type": "product",
        "invoice_policy": "order",
    }
    if unit:
        vals["uom_id"] = unit.id
        vals["uom_po_id"] = unit.id
    if existing:
        existing.write(vals)
        template = existing
        action = "updated"
    else:
        template = env["product.template"].create(vals)
        bind_xmlid(env, "product", record["legacy_id"], template)
        action = "created"
    variant = template.with_context(active_test=False).product_variant_ids[:1]
    if variant and not record.get("has_variants"):
        bind_xmlid(env, "variant", "simple_%s" % record["legacy_id"], variant)
        if record.get("default_code"):
            variant.default_code = record["default_code"]
    return _result(action, xml_id, template.id, extra={
        "variant_id": variant.id if variant else None,
    })


def configure_variants(env, record):
    template = browse_xmlid(env, "product", record["legacy_id"])
    xml_id = xmlid("product", record["legacy_id"])
    if not template:
        return _result("skipped", xml_id, extra={"reason": "PRODUCT_NOT_MAPPED"})
    mapped = []
    warned = []
    for line in record.get("attribute_lines") or []:
        attribute = browse_xmlid(env, "attribute", line["attribute_legacy_id"])
        values = env["product.attribute.value"]
        for value_id in line.get("value_legacy_ids") or []:
            value = browse_xmlid(env, "attribute_value", value_id)
            if value:
                values |= value
        if not attribute or not values:
            warned.append("ATTRIBUTE_LINE_INCOMPLETE:%s" % line["attribute_legacy_id"])
            continue
        existing = template.attribute_line_ids.filtered(
            lambda rec, attr=attribute: rec.attribute_id.id == attr.id
        )
        if existing:
            existing.value_ids = values
        else:
            template.write({
                "attribute_line_ids": [Command.create({
                    "attribute_id": attribute.id,
                    "value_ids": [Command.set(values.ids)],
                })],
            })
    template.invalidate_recordset()
    generated = template.with_context(active_test=False).product_variant_ids
    mapped_variant_ids = set()
    for variant in record.get("variants") or []:
        wanted = set(variant.get("value_legacy_ids") or [])
        match = None
        for candidate in generated:
            have = set()
            for ptav in candidate.product_template_attribute_value_ids:
                pav = ptav.product_attribute_value_id
                imd = env["ir.model.data"].sudo().search([
                    ("module", "=", "almohit_legacy"),
                    ("model", "=", "product.attribute.value"),
                    ("res_id", "=", pav.id),
                ], limit=1)
                if imd:
                    have.add(imd.name.replace("attribute_value_", "", 1))
            if have == wanted:
                match = candidate
                break
        if not match:
            warned.append("VARIANT_COMBINATION_MISSING:%s" % variant["legacy_id"])
            continue
        bind_xmlid(env, "variant", variant["legacy_id"], match)
        write_vals = {
            "default_code": variant.get("default_code") or False,
            "active": bool(variant.get("active", True)),
        }
        match.write(write_vals)
        mapped.append(_result("updated", xmlid("variant", variant["legacy_id"]), match.id))
        mapped_variant_ids.add(match.id)
    for extra in generated:
        if extra.id not in mapped_variant_ids and extra.active:
            extra.active = False
            warned.append("ARCHIVED_UNMAPPED_VARIANT:%s" % extra.id)
    return _result("updated", xml_id, template.id, extra={
        "variants": mapped,
        "warnings": warned,
    })


def apply_images(env, record):
    template = browse_xmlid(env, "product", record["legacy_id"])
    xml_id = xmlid("product", record["legacy_id"])
    if not template:
        return _result("skipped", xml_id, extra={"reason": "PRODUCT_NOT_MAPPED"})
    extra = {"gallery_attached": 0, "primary_written": False}
    force = bool(record.get("force"))
    has_image = bool(template.image_1920)
    if has_image and not force and not record.get("variant_image_base64"):
        return _result("skipped", xml_id, template.id, extra={"reason": "ALREADY_HAS_IMAGE"})
    if (not has_image or force) and record.get("image_base64"):
        template.write({"image_1920": record["image_base64"]})
        extra["primary_written"] = True
    variant = False
    if record.get("variant_legacy_id") and record.get("variant_image_base64"):
        variant = browse_xmlid(env, "variant", record["variant_legacy_id"])
        if variant and (force or not variant.image_variant_1920):
            variant.write({"image_variant_1920": record["variant_image_base64"]})
            extra["variant_written"] = True
    for index, attachment in enumerate(record.get("gallery") or []):
        att_xml = "gallery_%s_%s" % (record["legacy_id"], index)
        existing = browse_xmlid(env, "attachment", att_xml)
        vals = {
            "name": attachment.get("name") or ("gallery-%s" % index),
            "res_model": "product.template",
            "res_id": template.id,
            "type": "binary",
            "datas": attachment["data"],
            "mimetype": attachment.get("mimetype") or "image/jpeg",
        }
        if existing:
            existing.write(vals)
        else:
            created = env["ir.attachment"].create(vals)
            bind_xmlid(env, "attachment", att_xml, created)
        extra["gallery_attached"] += 1
    extra["gallery_omitted_from_storefront"] = extra["gallery_attached"]
    if extra["primary_written"] or extra["gallery_attached"] or extra.get("variant_written"):
        return _result("updated", xml_id, template.id, extra=extra)
    return _result("skipped", xml_id, template.id, extra={"reason": "NO_IMAGE_PAYLOAD"})


def apply_prices(env, record):
    wholesale = env.ref(WHOLESALE_XMLID, raise_if_not_found=False)
    xml_id = xmlid("price", record["legacy_id"])
    if not wholesale:
        return _result("skipped", xml_id, extra={"reason": "WHOLESALE_PRICELIST_MISSING"})
    if record.get("currency") and record["currency"] != wholesale.currency_id.name:
        return _result("skipped", xml_id, extra={
            "reason": "CURRENCY_MISMATCH",
            "currency": record.get("currency"),
            "pricelist_currency": wholesale.currency_id.name,
        })
    template = browse_xmlid(env, "product", record["product_legacy_id"])
    if not template:
        return _result("skipped", xml_id, extra={"reason": "PRODUCT_NOT_MAPPED"})
    variant = False
    if record.get("variant_legacy_id"):
        variant = browse_xmlid(env, "variant", record["variant_legacy_id"])
        if not variant:
            return _result("skipped", xml_id, extra={"reason": "VARIANT_NOT_MAPPED"})
    vals = {
        "pricelist_id": wholesale.id,
        "compute_price": "fixed",
        "fixed_price": float(record["amount"]),
        "min_quantity": 0.0,
        "applied_on": "0_product_variant" if variant else "1_product",
        "product_tmpl_id": template.id,
        "product_id": variant.id if variant else False,
    }
    if record.get("date_start"):
        vals["date_start"] = record["date_start"]
    if record.get("date_end"):
        vals["date_end"] = record["date_end"]
    existing = browse_xmlid(env, "price", record["legacy_id"])
    if existing:
        existing.write(vals)
        return _result("updated", xml_id, existing.id)
    created = env["product.pricelist.item"].create(vals)
    bind_xmlid(env, "price", record["legacy_id"], created)
    return _result("created", xml_id, created.id)


def apply_inventory(env, record):
    xml_id = xmlid("variant", record["variant_legacy_id"])
    variant = browse_xmlid(env, "variant", record["variant_legacy_id"])
    if not variant:
        return _result("skipped", xml_id, extra={"reason": "VARIANT_NOT_MAPPED"})
    if variant.type != "product":
        return _result("skipped", xml_id, extra={"reason": "NOT_STORABLE"})
    warehouse = env["stock.warehouse"].search(
        [("company_id", "=", env.company.id)],
        limit=1,
    )
    if not warehouse:
        return _result("skipped", xml_id, extra={"reason": "WAREHOUSE_MISSING"})
    location = warehouse.lot_stock_id
    qty = float(record.get("quantity") or 0.0)
    Quant = env["stock.quant"].with_user(SUPERUSER_ID).with_context(
        inventory_mode=True,
        set_inventory_quantity_auto_apply=True,
    )
    quant = Quant.search([
        ("product_id", "=", variant.id),
        ("location_id", "=", location.id),
    ], limit=1)
    if not quant:
        Quant.create({
            "product_id": variant.id,
            "location_id": location.id,
            "inventory_quantity": qty,
        })
        return _result("created", xml_id, variant.id, extra={"quantity": qty})
    quant.inventory_quantity = qty
    quant.action_apply_inventory()
    return _result("updated", xml_id, variant.id, extra={"quantity": qty})


PHASES = {
    "categories": upsert_category,
    "brands": upsert_brand,
    "attributes": upsert_attribute,
    "products": upsert_product,
    "variants": configure_variants,
    "images": apply_images,
    "prices": apply_prices,
    "inventory": apply_inventory,
}


def apply_batch(env, phase, records):
    handler = PHASES.get(phase)
    if not handler:
        return None, "UNKNOWN_PHASE"
    results = []
    for record in records:
        try:
            with env.cr.savepoint():
                results.append(handler(env, record))
        except Exception as exc:
            results.append(_result(
                "error",
                xmlid(phase[:-1] if phase.endswith("s") else phase, record.get("legacy_id")),
                extra={"reason": str(exc), "legacy_id": record.get("legacy_id")},
            ))
    created = sum(1 for row in results if row["action"] == "created")
    updated = sum(1 for row in results if row["action"] == "updated")
    skipped = sum(1 for row in results if row["action"] == "skipped")
    errors = sum(1 for row in results if row["action"] == "error")
    return {
        "phase": phase,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }, None
