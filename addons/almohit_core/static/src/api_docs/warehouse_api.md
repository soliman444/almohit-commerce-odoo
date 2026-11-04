# Almohit Warehouse Manager API (phases 1–7)

Module version: **17.0.1.67.0**

App clients use **warehouse IDs** only for receive / issue / transfer writes. Odoo keeps using `stock.location` internally (including child shelves and multi-step routes).

**Purchase receipts:** Buyer creates/confirms POs and may **read** receipts. Only Warehouse Manager posts stock (`perm_receipt`). Buyer `POST /buyer/receipts/{id}/receive` always returns `403 RECEIPT_HANDED_TO_WAREHOUSE` and never mutates quants.

All routes require an authenticated portal user with:

- role `warehouse_manager`
- group `almohit_core.group_almohit_warehouse_manager`
- `almohit_trade_status = approved`
- active `almohit.warehouse.assignment` with at least one warehouse in the **current company**
- endpoint-specific permission flags on the assignment

Namespace: `/almohit/api/warehouse/`

Cross-company warehouse IDs → `403 CROSS_COMPANY_DENIED`.  
Unassigned / wrong warehouse → `403 WAREHOUSE_NOT_ALLOWED` or not-found equivalents.  
Unapproved → `403 WAREHOUSE_APPROVAL_REQUIRED`.  
No assignment → `403 WAREHOUSE_ASSIGNMENT_REQUIRED`.  
Unknown request fields (including legacy location fields) → `400 INVALID_FIELDS`.

## Auth payload addition

```json
{
  "warehouse_access": {
    "warehouses": [{"id": 1, "name": "Main"}],
    "default_warehouse_id": 1,
    "permissions": ["stock_read", "receipt", "issue", "transfer_create", "transfer_receive", "packing", "packing_bom_manage"]
  }
}
```

## Receipt validation compatibility

ICP `almohit.receipt_validation_mode` (Settings → Almohit Warehouse):

| Value | Buyer receive | Warehouse receive |
|-------|---------------|-------------------|
| `warehouse_only` (**default**) | always `403 RECEIPT_HANDED_TO_WAREHOUSE` (no stock) | allowed |
| `both` (legacy) | same 403 stub — stock path removed | allowed |
| `buyer_only` (legacy) | same 403 stub — stock path removed | `403 RECEIPT_BUYER_ONLY` |

Quantity fields on receipt/issue lines:

- `ordered_qty` — demand
- `prepared_qty` — qty entered on the open move (not posted)
- `reserved_qty` — reserved on open moves
- `received_qty` / `done_qty` — **posted** qty only (`state=done`)
- `remaining_qty` — `ordered_qty - received_qty` (or done)

## Endpoints

### Read
- `GET /dashboard`
- `GET /my-warehouses`
- `GET /stock?warehouse_id=&product_id=&page=` — each item includes `stock_role`: `storage` (lot_stock tree), `dispatch_staging` (Output; staged for distributor, not free to re-dispatch), `pack`, `input`, or `other`. Warehouse mapping includes archived WH locations (`active_test=False`). Does not change `free_qty` used by dispatch.
- `GET /movements?date_from=&date_to=&page=`
- `GET /stock-variances?page=&page_size=`

### Receipts
- `GET /receipts?status=pending&kind=purchase|return`
- `GET /receipts/{id}` — lines (`move_id`, qtys), `available_warehouses`; `current_warehouse` / `suggested_warehouse` are informational only
- `POST /receipts/{id}/receive` — **required per line:** `move_id`, `quantity_done`, `warehouse_id` (from `available_warehouses` / `GET /my-warehouses`). No request-level `warehouse_id`. No location fields. Different warehouses split onto native incoming pickings before validate.

```json
{
  "lines": [
    {"move_id": 263, "quantity_done": 10, "warehouse_id": 1},
    {"move_id": 264, "quantity_done": 20, "warehouse_id": 2}
  ]
}
```

Each line is received into the selected warehouse's incoming operation type (Input or Stock per that warehouse's receipt steps). Multi-warehouse lines are split onto separate native pickings, then validated atomically with the purchase link preserved.

### Issues (dispatch ≠ distributor delivery)
- `GET /distributors` — active approved distributor users (`id`, `name`) for optional assign on dispatch (`perm_issue`)
- `GET /issues?status=pending|dispatched|done|all`
- `GET /issues/{id}` — lines (`move_id`, qtys), `available_warehouses` (`free_qty` = dispatchable qty on `lot_stock` + child shelves only; excludes Output); `current_warehouse` / `suggested_warehouse` are informational only; `distributor_user_id` (= native `stock.picking.user_id`); `dispatch_picking_id` (primary) + `dispatch_picking_ids` / `result_pickings` (all stock→output pickings)
- `POST /issues/{id}/dispatch` — **required per line:** `move_id`, `quantity_done`, `warehouse_id`. Optional request-level `distributor_user_id` (sets `stock.picking.user_id`; omit to leave assignee unchanged; `null`/`0` rejected). No request-level `warehouse_id`. No location fields.

```json
{
  "distributor_user_id": 275,
  "lines": [
    {"move_id": 271, "quantity_done": 3, "warehouse_id": 2},
    {"move_id": 272, "quantity_done": 5, "warehouse_id": 1}
  ]
}
```

Creates one native internal stock→output picking per selected warehouse (child shelves included via Odoo assign). Does not touch `user_id` and does not validate the customer outgoing picking. Multi-warehouse lines return `result_pickings`. Customer delivery moves are relocated to each warehouse Output for distributor pickup.

### Transfers
- `GET /transfers?direction=incoming|outgoing|all&status=`
- `POST /transfers` — creates the transfer **and** dispatches stock→Transit in one request. State becomes `dispatched` (awaits destination receive). Optional `idempotency_key` prevents duplicate create+dispatch on retry.
- `GET /transfers/{id}`
- `POST /transfers/{id}/receive`
- `POST /transfers/{id}/cancel`
- `POST /transfers/{id}/reject`
- `POST /transfers/{id}/close`
- `GET /stock-variances`
- `POST /discrepancies/{id}/resolve`

```json
// create + dispatch
{
  "source_warehouse_id": 1,
  "dest_warehouse_id": 2,
  "idempotency_key": "wh-tr-2026-001",
  "lines": [{"product_id": 9, "quantity": 5}]
}

// receive
{
  "lines": [
    {"line_id": 3, "quantity": 4, "reason": "missing"}
  ]
}

// resolve discrepancy
{"note": "Written off after QC"}
```

Optional create fields: `note`, `idempotency_key`; optional line field: `uom_id`. Location fields are not accepted. Source and destination warehouses must both be assigned. No separate `/dispatch` route.

States: `draft → requested → dispatched → partially_received|received|discrepancy → closed` (+ `rejected` / `cancelled`). `POST /transfers` returns `dispatched` on success.

### Packing / manufacturing
Native Odoo `mrp.bom` / `mrp.production`. Execution is one atomic POST. App must **not** send `location_id` / `location_dest_id`.

Permissions:
- Read BoMs / MOs + execute packing: `packing` (`perm_packing`)
- Create / update BoMs: `packing_bom_manage` (`perm_packing_bom_manage`) — independent of packing

- `GET /packing/boms?q=&product_id=&page=`
- `POST /packing/boms` — body: `{ product_id, warehouse_id, quantity, components[{product_id,quantity}], byproducts?[{product_id,quantity,cost_share?}], code? }`
  - Backend sets `picking_type_id` from the warehouse manufacturing operation type.
  - Shared BoMs (no operation type) are not created by this API.
- `GET /packing/boms/{id}`
- `PATCH /packing/boms/{id}` — same writable fields; refuses shared BoMs (`BOM_SHARED_READONLY`) and BoMs of unassigned warehouses (`BOM_WAREHOUSE_DENIED`); refuses edit when confirmed/done MOs exist (`BOM_IN_USE`).
- `GET /packing/orders?status=&q=&page=`
- `GET /packing/orders/{id}` — detail includes `components`, `finished_moves` (main + by-products; use `is_byproduct`), and `scraps`. No separate `main_finished` / `byproducts` response arrays.
- `POST /packing/orders/execute` — single atomic create → confirm/assign → complete. Body example:
  ```json
  {
    "bom_id": 15,
    "warehouse_id": 1,
    "quantity": 50,
    "idempotency_key": "packing-2026-001",
    "finished_qty": 45,
    "finished_lot_name": "POM-LOT-001",
    "create_backorder": false,
    "components": [{"product_id": 100, "quantity": 50}],
    "byproducts": [{"product_id": 101, "quantity": 20}],
    "scraps": [{"product_id": 100, "quantity": 5}]
  }
  ```
  - `quantity` = planned MO qty; `finished_qty` = actual finished qty (required; not an alias of `quantity`).
  - `idempotency_key` is **required**. Same key on a done MO returns that MO (`origin = almohit:packing:{partner_id}:{key}`). Incomplete MO with same key → `PACKING_ORDER_IDEMPOTENCY_CONFLICT`.
  - Failure after create rolls back the whole request (no leftover draft/confirmed MO).
  - By-products must already exist on the MO/BoM (`BYPRODUCT_NOT_ON_BOM` otherwise).
  - Scrap via `stock.scrap` once (do **not** also put the same units in `components`).
  - No `location_id` / `location_dest_id`. Uses Odoo `button_mark_done` + wizard resolution.

### Inventory / opening balances
- `GET /inventory`
- `GET /inventory/{id}`
- `POST /inventory/import`
- `POST /inventory/{id}/approve`

```json
{
  "warehouse_id": 1,
  "session_type": "opening",
  "filename": "opening.xlsx",
  "file_base64": "<base64>"
}
```

### Example errors
| Code | HTTP | When |
|------|------|------|
| `WAREHOUSE_APPROVAL_REQUIRED` | 403 | trade not approved |
| `WAREHOUSE_ASSIGNMENT_REQUIRED` | 403 | no warehouses assigned |
| `WAREHOUSE_PERMISSION_DENIED` | 403 | missing assignment flag |
| `CROSS_COMPANY_DENIED` | 403 | warehouse/company mismatch |
| `INVALID_FIELDS` | 400 | unknown / legacy location fields |
| `RECEIPT_BUYER_ONLY` / `RECEIPT_HANDED_TO_WAREHOUSE` | 403 | receipt mode gate |
| `IMPORT_DUPLICATE` | 409 | same file checksum |
| `ISSUE_ALREADY_DISPATCHED` | 409 | idempotent dispatch retry |
| `TRANSFER_NEEDS_RECONCILE` | 409 | reject after stock left source |
