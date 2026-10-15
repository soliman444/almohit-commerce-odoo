from odoo import http
from odoo.http import request
from odoo.tools import file_path

from ..services.api import CORS_HEADERS


def _file_response(relative_path, content_type):
    path = file_path(f"almohit_core/static/src/api_docs/{relative_path}")
    with open(path, "rb") as handle:
        payload = handle.read()
    headers = {
        **CORS_HEADERS,
        "Content-Type": content_type,
        "Cache-Control": "no-store",
    }
    return request.make_response(payload, headers=list(headers.items()))


class AlmohitDocsController(http.Controller):

    @http.route(
        "/almohit/api/docs",
        type="http",
        auth="none",
        methods=["GET", "OPTIONS"],
        csrf=False,
    )
    def docs(self, **kwargs):
        if request.httprequest.method == "OPTIONS":
            return http.Response("", status=204, headers=CORS_HEADERS)
        return _file_response("index.html", "text/html; charset=utf-8")

    @http.route(
        "/almohit/api/openapi.json",
        type="http",
        auth="none",
        methods=["GET", "OPTIONS"],
        csrf=False,
    )
    def openapi(self, **kwargs):
        if request.httprequest.method == "OPTIONS":
            return http.Response("", status=204, headers=CORS_HEADERS)
        return _file_response("openapi.json", "application/json; charset=utf-8")
