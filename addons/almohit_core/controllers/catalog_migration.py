import json

from odoo import http
from odoo.http import request

from ..services.api import error, success
from ..services.catalog_migration import apply_batch, migration_env


class AlmohitCatalogMigrationController(http.Controller):

    @http.route(
        "/almohit/api/migration/catalog/ping",
        type="http",
        auth="public",
        methods=["GET", "POST"],
        csrf=False,
    )
    def ping(self, **kwargs):
        env = migration_env(request)
        if env is None:
            return error(
                "MIGRATION_FORBIDDEN",
                "Catalog migration requires a system admin or migration token.",
                403,
            )
        wholesale = env.ref(
            "almohit_core.pricelist_wholesale",
            raise_if_not_found=False,
        )
        templates = env["product.template"].with_context(active_test=False).search_count([])
        mapped = env["ir.model.data"].search_count([("module", "=", "almohit_legacy")])
        return success({
            "ok": True,
            "wholesale_pricelist_id": wholesale.id if wholesale else None,
            "template_count": templates,
            "mapped_xmlids": mapped,
        })

    @http.route(
        "/almohit/api/migration/catalog/batch",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def batch(self, **kwargs):
        env = migration_env(request)
        if env is None:
            return error(
                "MIGRATION_FORBIDDEN",
                "Catalog migration requires a system admin or migration token.",
                403,
            )
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except json.JSONDecodeError:
            return error("INVALID_JSON", "Invalid JSON body.", 400)
        if not isinstance(payload, dict):
            return error("INVALID_JSON", "Invalid JSON body.", 400)
        phase = payload.get("phase")
        records = payload.get("records")
        if not isinstance(records, list):
            return error("INVALID_JSON", "records must be a list.", 400)
        data, err = apply_batch(env, phase, records)
        if err:
            return error(err, "Unknown migration phase.", 400)
        env.cr.commit()
        return success(data)
