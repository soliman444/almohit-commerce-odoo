import json

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