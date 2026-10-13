MODULE = "almohit_legacy"

KINDS = {
    "category": "category",
    "brand": "brand",
    "attribute": "attribute",
    "attribute_value": "attribute_value",
    "product": "product",
    "variant": "variant",
    "price": "price",
    "attachment": "attachment",
}


def xmlid_name(kind, legacy_id):
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(legacy_id))
    return "%s_%s" % (kind, safe)


def xmlid(kind, legacy_id):
    return "%s.%s" % (MODULE, xmlid_name(kind, legacy_id))


def browse_xmlid(env, kind, legacy_id):
    return env.ref(xmlid(kind, legacy_id), raise_if_not_found=False)


def bind_xmlid(env, kind, legacy_id, record):
    name = xmlid_name(kind, legacy_id)
    Imd = env["ir.model.data"].sudo()
    existing = Imd.search(
        [("module", "=", MODULE), ("name", "=", name)],
        limit=1,
    )
    if existing:
        if existing.model != record._name or existing.res_id != record.id:
            existing.write({
                "model": record._name,
                "res_id": record.id,
                "noupdate": True,
            })
        return existing
    return Imd.create({
        "module": MODULE,
        "name": name,
        "model": record._name,
        "res_id": record.id,
        "noupdate": True,
    })
