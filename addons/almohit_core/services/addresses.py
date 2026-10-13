ALLOWED_ADDRESS_TYPES = ("delivery", "invoice")


def _named_rel(record):
    if not record:
        return None
    return {
        "id": record.id,
        "name": record.name,
    }


def serialize_address(partner):
    country = partner.country_id
    return {
        "id": partner.id,
        "type": partner.type,
        "name": partner.name or "",
        "street": partner.street or "",
        "street2": partner.street2 or "",
        "city": partner.city or "",
        "state": _named_rel(partner.state_id),
        "zip": partner.zip or "",
        "country": (
            {
                "id": country.id,
                "name": country.name,
                "code": country.code or None,
            }
            if country
            else None
        ),
        "phone": partner.phone or "",
    }


def address_domain(commercial):
    return [
        ("parent_id", "=", commercial.id),
        ("id", "!=", commercial.id),
        ("type", "in", list(ALLOWED_ADDRESS_TYPES)),
    ]


def search_addresses(env, commercial):
    return env["res.partner"].sudo().search(
        address_domain(commercial),
        order="type, id",
    )


def owned_address(env, commercial, address_id):
    try:
        address_id = int(address_id)
    except (TypeError, ValueError):
        return env["res.partner"].sudo().browse()
    return env["res.partner"].sudo().search(
        [("id", "=", address_id)] + address_domain(commercial),
        limit=1,
    )


def _parse_id(value):
    if value is None or value is False or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value):
    if value is None or value is False:
        return ""
    return str(value).strip()


def parse_address_payload(env, payload, *, partial=False):
    if not isinstance(payload, dict):
        return None, ("INVALID_JSON", "Invalid JSON body.", 400)

    payload = dict(payload)
    payload.pop("parent_id", None)
    payload.pop("partner_id", None)

    values = {}

    if not partial or "type" in payload:
        address_type = _clean_text(payload.get("type"))
        if address_type not in ALLOWED_ADDRESS_TYPES:
            return None, (
                "ADDRESS_TYPE_INVALID",
                "Address type must be delivery or invoice.",
                400,
            )
        values["type"] = address_type

    if not partial or "name" in payload:
        name = _clean_text(payload.get("name"))
        if not name:
            return None, ("NAME_REQUIRED", "Name is required.", 400)
        values["name"] = name

    if not partial or "street" in payload:
        street = _clean_text(payload.get("street"))
        if not street:
            return None, ("STREET_REQUIRED", "Street is required.", 400)
        values["street"] = street

    if not partial or "street2" in payload:
        values["street2"] = _clean_text(payload.get("street2")) or False

    if not partial or "city" in payload:
        city = _clean_text(payload.get("city"))
        if not city:
            return None, ("CITY_REQUIRED", "City is required.", 400)
        values["city"] = city

    if not partial or "zip" in payload:
        values["zip"] = _clean_text(payload.get("zip")) or False

    if not partial or "phone" in payload:
        values["phone"] = _clean_text(payload.get("phone")) or False

    if not partial or "country_id" in payload or "country_code" in payload:
        country = _resolve_country(env, payload)
        if country is None:
            return None, ("COUNTRY_REQUIRED", "Country is required.", 400)
        if country is False:
            return None, ("COUNTRY_NOT_FOUND", "Country not found.", 400)
        values["country_id"] = country.id

    if not partial or "state_id" in payload:
        if "state_id" not in payload or payload.get("state_id") in (None, False, ""):
            values["state_id"] = False
        else:
            state_id = _parse_id(payload.get("state_id"))
            if not state_id:
                return None, ("STATE_NOT_FOUND", "State not found.", 400)
            state = env["res.country.state"].sudo().browse(state_id)
            if not state.exists():
                return None, ("STATE_NOT_FOUND", "State not found.", 400)
            values["state_id"] = state.id

    return values, None


def _resolve_country(env, payload):
    country_id = _parse_id(payload.get("country_id"))
    if country_id:
        country = env["res.country"].sudo().browse(country_id)
        return country if country.exists() else False
    code = _clean_text(payload.get("country_code")).upper()
    if not code:
        return None
    country = env["res.country"].sudo().search([("code", "=", code)], limit=1)
    return country if country else False


def validate_state_country(env, values, existing=None):
    country_id = values.get("country_id")
    if country_id is None and existing:
        country_id = existing.country_id.id
    if "state_id" in values:
        state_id = values.get("state_id")
    elif existing:
        state_id = existing.state_id.id
    else:
        state_id = False
    if not state_id:
        return None
    state = env["res.country.state"].sudo().browse(state_id)
    if not state.exists():
        return ("STATE_NOT_FOUND", "State not found.", 400)
    if country_id and state.country_id.id != country_id:
        return (
            "STATE_COUNTRY_MISMATCH",
            "State does not belong to the selected country.",
            400,
        )
    return None
