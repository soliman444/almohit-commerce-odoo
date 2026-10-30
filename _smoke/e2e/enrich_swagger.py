"""Enrich OpenAPI + Swagger UI for warehouse release docs."""
import json
from pathlib import Path

ROOTS = [
    Path(r"G:\odoo17-docker\addons\almohit_core\static\src\api_docs"),
    Path(r"G:\Wholesale Commerce Platform\odoo\addons\almohit_core\static\src\api_docs"),
]

VERSION = "17.0.1.57.0"

INDEX_HTML = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Almohit App API {VERSION}</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css" />
    <style>
      body {{ margin: 0; background: #f7f7f5; }}
      .topbar {{ display: none; }}
      .almohit-docs-banner {{
        font-family: Georgia, "Times New Roman", serif;
        padding: 14px 24px;
        background: linear-gradient(120deg, #1f3d2a, #2f5d3a 55%, #c4a574);
        color: #f7f3ea;
      }}
      .almohit-docs-banner h1 {{
        margin: 0;
        font-size: 1.25rem;
        font-weight: 600;
        letter-spacing: 0.02em;
      }}
      .almohit-docs-banner p {{
        margin: 6px 0 0;
        font-size: 0.9rem;
        opacity: 0.92;
      }}
      .almohit-docs-banner a {{ color: #fff4d6; }}
    </style>
  </head>
  <body>
    <header class="almohit-docs-banner">
      <h1>Almohit App API · {VERSION}</h1>
      <p>
        Swagger UI · session cookie auth ·
        <a href="/almohit/api/openapi.json">openapi.json</a> ·
        <a href="/almohit/api/docs/warehouse">warehouse notes</a>
      </p>
    </header>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
    <script>
      window.onload = function () {{
        window.ui = SwaggerUIBundle({{
          url: "/almohit/api/openapi.json",
          dom_id: "#swagger-ui",
          deepLinking: true,
          filter: true,
          tryItOutEnabled: true,
          persistAuthorization: true,
          displayRequestDuration: true,
          docExpansion: "list",
          tagsSorter: "alpha",
          operationsSorter: "alpha",
        }});
      }};
    </script>
  </body>
</html>
"""

RECEIVE_EXAMPLE = {
    "lines": [
        {"move_id": 10, "quantity_done": 5, "location_dest_id": 8},
        {
            "move_id": 11,
            "destinations": [
                {"location_id": 8, "quantity": 3},
                {"location_id": 26, "quantity": 2},
            ],
        },
    ]
}

DISPATCH_EXAMPLE = {
    "lines": [{"move_id": 55, "quantity_done": 2, "location_id": 8}]
}

TRANSFER_EXAMPLE = {
    "source_warehouse_id": 1,
    "dest_warehouse_id": 2,
    "lines": [{"product_id": 158, "quantity": 5, "location_id": 8}],
}

IMPORT_EXAMPLE = {
    "warehouse_id": 1,
    "session_type": "opening",
    "filename": "opening.xlsx",
    "file_base64": "<base64-csv-or-xlsx>",
}


def enrich(doc: dict) -> dict:
    doc["info"]["title"] = "Almohit App API"
    doc["info"]["version"] = VERSION
    desc = doc["info"].get("description") or ""
    if "warehouse_api.md" not in desc:
        desc += (
            "\n\n**Warehouse Manager docs.** Interactive paths under tag `Warehouse`. "
            "Human-readable notes: `/almohit/api/docs/warehouse` (warehouse_api.md). "
            f"Module version {VERSION}."
        )
        doc["info"]["description"] = desc

    auth = doc["components"]["schemas"].get("AuthUser", {})
    props = auth.setdefault("properties", {})
    role = props.get("role", {"type": "string"})
    role["enum"] = ["merchant", "buyer", "distributor", "marketer", "warehouse_manager"]
    props["role"] = role
    props["warehouse_access"] = {
        "$ref": "#/components/schemas/WarehouseAccess",
        "description": "Present for warehouse_manager sessions after login/me.",
        "nullable": True,
    }
    props["latitude"] = {"type": "number", "nullable": True}
    props["longitude"] = {"type": "number", "nullable": True}
    auth["properties"] = props
    doc["components"]["schemas"]["AuthUser"] = auth

    # Attach examples on key warehouse request bodies
    examples = {
        "/almohit/api/warehouse/receipts/{picking_id}/receive": RECEIVE_EXAMPLE,
        "/almohit/api/warehouse/issues/{picking_id}/dispatch": DISPATCH_EXAMPLE,
        "/almohit/api/warehouse/transfers": TRANSFER_EXAMPLE,
        "/almohit/api/warehouse/inventory/import": IMPORT_EXAMPLE,
    }
    for path, example in examples.items():
        op = doc.get("paths", {}).get(path, {})
        post = op.get("post")
        if not post:
            continue
        body = post.setdefault("requestBody", {}).setdefault("content", {}).setdefault(
            "application/json", {}
        )
        body["example"] = example

    # Transfer receive example
    recv_path = "/almohit/api/warehouse/transfers/{transfer_id}/receive"
    post = doc.get("paths", {}).get(recv_path, {}).get("post")
    if post:
        body = post.setdefault("requestBody", {}).setdefault("content", {}).setdefault(
            "application/json", {}
        )
        body["schema"] = {
            "type": "object",
            "required": ["lines"],
            "properties": {
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_id": {"type": "integer"},
                            "quantity": {"type": "number"},
                            "location_id": {"type": "integer"},
                            "reason": {
                                "type": "string",
                                "enum": [
                                    "missing",
                                    "excess",
                                    "damaged",
                                    "rejected",
                                    "other",
                                ],
                            },
                        },
                    },
                }
            },
        }
        body["example"] = {
            "lines": [
                {
                    "line_id": 3,
                    "quantity": 4,
                    "location_id": 18,
                    "reason": "missing",
                }
            ]
        }

    return doc


def main():
    for root in ROOTS:
        root.mkdir(parents=True, exist_ok=True)
        openapi_path = root / "openapi.json"
        doc = json.loads(openapi_path.read_text(encoding="utf-8"))
        doc = enrich(doc)
        openapi_path.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
        # ensure warehouse md present on docker too
        md_src = Path(
            r"G:\Wholesale Commerce Platform\odoo\addons\almohit_core\static\src\api_docs\warehouse_api.md"
        )
        if md_src.exists():
            text = md_src.read_text(encoding="utf-8")
            text = text.replace("17.0.1.56.0", VERSION).replace(
                "17.0.1.55.0", VERSION
            )
            if f"**{VERSION}**" not in text and "Module version:" in text:
                import re

                text = re.sub(
                    r"Module version:\s*\*\*[^*]+\*\*",
                    f"Module version: **{VERSION}**",
                    text,
                    count=1,
                )
            (root / "warehouse_api.md").write_text(text, encoding="utf-8")
        print("updated", root)


if __name__ == "__main__":
    main()
