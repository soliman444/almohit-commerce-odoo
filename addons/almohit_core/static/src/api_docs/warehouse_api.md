# Almohit Warehouse Manager API (phases 1–7)

Module version: **17.0.1.57.0**

All routes require an authenticated portal user with:

- role `warehouse_manager`
- group `almohit_core.group_almohit_warehouse_manager`
- `almohit_trade_status = approved`
- active `almohit.warehouse.assignment` with at least one warehouse in the **current company**
- endpoint-specific permission flags on the assignment

Namespace: `/almohit/api/warehouse/`

Cross-company warehouse or location IDs → `403 CROSS_COMPANY_DENIED`.  
Unassigned / wrong warehouse → `403 WAREHOUSE_NOT_ALLOWED` or not-found equivalents.  
Unapproved → `403 WAREHOUSE_APPROVAL_REQUIRED`.  
No assignment → `403 WAREHOUSE_ASSIGNMENT_REQUIRED`.

## Auth payload addition

```32
{
  "warehouse_access": {
    "warehouses": [{"id": 1, "name": "Main"}],
    "default_warehouse_id": 1,
    "permissions": ["stock_read", "receipt", "issue", "transfer_create", "transfer_receive"]
  }
}
```

## Receipt validation compatibility

ICP `almohit.receipt_validation_mode` (Settings → Almohit Warehouse):

| Value | Buyer receive | Warehouse receive |
|-------|---------------|-------------------|
| `both` (default) | allowed | allowed |
| `warehouse_only` | `403 RECEIPT_HANDED_TO_WAREHOUSE` | allowed |
| `buyer_only` | allowed | `403 RECEIPT_BUYER_ONLY` |

## Endpoints

### Read
- `GET /dashboard`
- `GET /my-warehouses`
- `GET /stock?warehouse_id=&product_id=&page=`
- `GET /movements?date_from=&date_to=&page=`

### Receipts
- `GET /receipts?status=pending&kind=purchase|return`
- `GET /receipts/{id}`
- `POST /receipts/{id}/receive`

```json
{
  "lines": [
    {"move_id": 10, "quantity_done": 5, "location_dest_id": 17},
    {
      "move_id": 11,
      "destinations": [
        {"location_id": 17, "quantity": 3},
        {"location_id": 18, "quantity": 2}
      ]
    }
  ]
}
```

### Issues (dispatch ≠ distributor delivery)
- `GET /issues?status=pending|dispatched|done|all`
- `GET /issues/{id}`
- `POST /issues/{id}/dispatch`

```json
{"lines": [{"move_id": 55, "quantity_done": 2, "location_id": 12}]}
```

Requires a **distinct** Output/Pack location. Creates internal stock→output picking only; does not touch `user_id` and does not validate the customer outgoing picking.

### Transfers
- `GET /transfers?direction=incoming|outgoing|all&status=`
- `POST /transfers`
- `GET /transfers/{id}`
- `POST /transfers/{id}/dispatch`
- `POST /transfers/{id}/receive`
- `POST /transfers/{id}/cancel` — source WH, states `draft|requested`
- `POST /transfers/{id}/reject` — dest WH, only before stock left (`requested`); after dispatch returns `409 TRANSFER_NEEDS_RECONCILE`
- `POST /transfers/{id}/close` — no open discrepancies; states `received|partially_received|discrepancy`
- `POST /discrepancies/{id}/resolve`

```json
// create
{
  "source_warehouse_id": 1,
  "dest_warehouse_id": 2,
  "lines": [{"product_id": 9, "quantity": 5, "location_id": 12}]
}

// receive (partial / discrepant)
{
  "lines": [
    {"line_id": 3, "quantity": 4, "location_id": 20, "reason": "missing"}
  ]
}

// resolve discrepancy
{"note": "Written off after QC"}
```

States: `draft → requested → dispatched → partially_received|received|discrepancy → closed` (+ `rejected` / `cancelled`).

### Inventory / opening balances
- `GET /inventory`
- `GET /inventory/{id}`
- `POST /inventory/import` — CSV **or** XLSX (`filename` ending `.xlsx` or zip sniff)
- `POST /inventory/{id}/approve`

```json
{
  "warehouse_id": 1,
  "session_type": "opening",
  "filename": "opening.xlsx",
  "file_base64": "<base64>"
}
```

Headers: `default_code` | `sku` | `barcode`, and `quantity`.  
Duplicate checksum → `409 IMPORT_DUPLICATE`.  
Approve applies counted qty via native inventory adjustment (superuser after assignment checks). Requires `perm_receipt` or `perm_issue`.

### Example errors
| Code | HTTP | When |
|------|------|------|
| `WAREHOUSE_APPROVAL_REQUIRED` | 403 | trade not approved |
| `WAREHOUSE_ASSIGNMENT_REQUIRED` | 403 | no warehouses assigned |
| `WAREHOUSE_PERMISSION_DENIED` | 403 | missing assignment flag |
| `CROSS_COMPANY_DENIED` | 403 | warehouse/company mismatch |
| `RECEIPT_BUYER_ONLY` / `RECEIPT_HANDED_TO_WAREHOUSE` | 403 | receipt mode gate |
| `IMPORT_DUPLICATE` | 409 | same file checksum |
| `ISSUE_ALREADY_DISPATCHED` | 409 | idempotent dispatch retry |
| `TRANSFER_NEEDS_RECONCILE` | 409 | reject after stock left source |
