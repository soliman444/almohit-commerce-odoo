import json
from datetime import datetime

from odoo import http
from odoo.http import request


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Max-Age": "86400",
}


def json_response(data, status=200):
    return http.Response(
        json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        ),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def success(data=None, status=200):
    body = {
        "ok": True,
        "data": data,
    }

    return json_response(body, status=status)


def read_json_body():
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


def parse_pagination(kwargs):
    """Parse Products-compatible page/page_size query params.

    Defaults: page=1, page_size=20. page >= 1, page_size 1..100.
    Returns (page, page_size, error_response).
    """
    try:
        page = 1 if kwargs.get("page") in (None, "") else int(kwargs.get("page"))
        page_size = (
            20 if kwargs.get("page_size") in (None, "") else int(kwargs.get("page_size"))
        )
    except (TypeError, ValueError):
        return None, None, error(
            "INVALID_PAGINATION",
            "page must be >= 1 and page_size must be between 1 and 100.",
            400,
        )
    if page < 1 or page_size < 1 or page_size > 100:
        return None, None, error(
            "INVALID_PAGINATION",
            "page must be >= 1 and page_size must be between 1 and 100.",
            400,
        )
    return page, page_size, None


def _query_scalar(kwargs, name):
    raw = kwargs.get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    return raw


def parse_iso_date_param(kwargs, name):
    raw = _query_scalar(kwargs, name)
    if raw in (None, ""):
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), None
    except ValueError:
        return None, error(
            "INVALID_FILTER",
            f"{name} must be an ISO date (YYYY-MM-DD).",
            400,
        )


def parse_date_range_params(kwargs):
    date_from, err = parse_iso_date_param(kwargs, "date_from")
    if err:
        return None, None, err
    date_to, err = parse_iso_date_param(kwargs, "date_to")
    if err:
        return None, None, err
    if date_from and date_to and date_from > date_to:
        return None, None, error(
            "INVALID_FILTER",
            "date_from must be on or before date_to.",
            400,
        )
    return date_from, date_to, None


def paginated_success(records, serialize_one, page, page_size):
    total = len(records)
    pages = 0 if total == 0 else (total + page_size - 1) // page_size
    offset = (page - 1) * page_size
    page_records = records[offset:offset + page_size]
    return success({
        "items": [serialize_one(record) for record in page_records],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        },
    })


def error(code, message, status=400, extra=None):
    err = {
        "code": code,
        "message": message,
    }
    if extra:
        err.update(extra)
    body = {
        "ok": False,
        "error": err,
    }

    return json_response(body, status=status)