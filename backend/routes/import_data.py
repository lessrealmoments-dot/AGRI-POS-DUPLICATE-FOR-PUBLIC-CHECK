"""
Import Center Routes
====================
Handles bulk data import from Excel (.xlsx, .xls) and CSV files.
Supports:
  - Product catalog import (global)
  - Inventory seed import (branch-specific, admin PIN required)

Flow:
  1. POST /api/import/parse     — upload file, return {headers, sample_rows, all_rows, total}
  2. POST /api/import/products  — import with column mapping, returns {imported, skipped, errors}
  3. POST /api/import/inventory-seed — set initial inventory (admin PIN)
  4. GET  /api/import/template/{type} — download CSV template
"""
import io
import csv
import re
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from config import db
from utils import get_current_user, check_perm, now_iso, new_id, verify_password

router = APIRouter(prefix="/import", tags=["Import"])


def _read_file(content: bytes, filename: str) -> list[dict]:
    """Parse .xls, .xlsx, or .csv file into list of row dicts."""
    name = filename.lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]

    elif name.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        result = []
        for row in rows[1:]:
            if all(v is None for v in row):
                continue
            d = {}
            for i in range(len(headers)):
                if headers[i]:  # Skip columns with empty headers
                    d[headers[i]] = row[i] if row[i] is not None else ""
            result.append(d)
        wb.close()
        return result

    elif name.endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(file_contents=content)
        ws = wb.sheet_by_index(0)
        if ws.nrows == 0:
            return []
        headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
        result = []
        for r in range(1, ws.nrows):
            row = {}
            for c in range(ws.ncols):
                if headers[c]:  # Skip columns with empty headers
                    row[headers[c]] = ws.cell_value(r, c)
            if all(str(v).strip() == "" for v in row.values()):
                continue
            result.append(row)
        return result

    else:
        raise ValueError("Unsupported file format. Use .csv, .xlsx, or .xls")


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if val not in (None, "", " ") else default
    except (ValueError, TypeError):
        return default


def _safe_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _generate_sku(name: str) -> str:
    """Auto-generate a short SKU from the product name."""
    slug = re.sub(r"[^A-Z0-9]", "", name.upper())[:8]
    return f"P-{slug}-{new_id()[:4].upper()}"


# =============================================================================
# QUICKBOOKS COLUMN PRESETS
# =============================================================================
QB_PRESET = {
    "name": "Product/Service Name",
    "unit": "SKU",                   # QB's SKU field is actually the unit
    "description": "Sales Description",
    "product_type": "Type",          # Inventory → stockable, Service → service
    "retail_price": "Sales Price / Rate",
    "cost_price": "Purchase Cost",
    "reorder_point": "Reorder Point",
    "quantity": "Quantity On Hand",  # for inventory seed
}

# Known preset configurations
COLUMN_PRESETS = {
    "quickbooks": {
        "label": "QuickBooks Online Export",
        "mapping": QB_PRESET,
        "notes": "QB's 'SKU' column is treated as Unit of Measurement. Quantity On Hand is used only in Inventory Seed import.",
    },
    "agripos": {
        "label": "AgriPOS Template",
        "mapping": {
            "name": "Product Name",
            "sku": "SKU",
            "category": "Category",
            "unit": "Unit",
            "description": "Description",
            "product_type": "Type",
            "retail_price": "Retail Price",
            "wholesale_price": "Wholesale Price",
            "cost_price": "Cost Price",
            "reorder_point": "Reorder Point",
            "quantity": "Quantity",
        },
    },
}


@router.get("/presets")
async def get_presets(user=Depends(get_current_user)):
    """Return available column mapping presets."""
    return COLUMN_PRESETS


@router.post("/parse")
async def parse_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    """
    Upload a file and return its headers + first 10 rows for preview.
    Also returns total row count so the user knows what they're importing.
    The full rows are NOT returned here — the client re-uploads on import.
    """
    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {str(e)}")

    if not rows:
        raise HTTPException(status_code=400, detail="File is empty or has no data rows")

    headers = list(rows[0].keys()) if rows else []
    # Clean empty headers
    headers = [h for h in headers if h.strip()]

    return {
        "filename": file.filename,
        "total_rows": len(rows),
        "headers": headers,
        "sample_rows": rows[:10],
    }


@router.post("/products")
async def import_products(
    file: UploadFile = File(...),
    mapping: str = Form(""),        # JSON string: {"name":"Product/Service Name", ...}
    branch_id: str = Form(""),      # Optional: branch for branch-specific prices
    user=Depends(get_current_user)
):
    """
    Import products from uploaded file using specified column mapping.
    Returns: { imported, skipped (with reason), errors }
    """
    check_perm(user, "products", "create")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid column mapping JSON")

    if not col_map.get("name"):
        raise HTTPException(status_code=400, detail="Column mapping must include 'name' field")

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get existing schemes for price mapping
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0}).to_list(50)
    scheme_keys = {s["key"]: s["name"] for s in schemes}

    # Discover which scheme keys the user mapped via *_price columns (e.g. wholesale_price, retail_price).
    # We auto-create any missing schemes before importing so user-mapped prices are never silently dropped.
    mapped_scheme_keys = set()
    for map_key in col_map:
        if map_key.endswith("_price") and map_key not in ("cost_price",) and col_map.get(map_key):
            mapped_scheme_keys.add(map_key[:-len("_price")])  # strip "_price" → e.g. "wholesale"

    schemes_auto_created = []
    for sk in mapped_scheme_keys:
        if sk and sk not in scheme_keys:
            new_scheme = {
                "id": new_id(),
                "name": sk.replace("_", " ").title(),
                "key": sk,
                "description": f"Auto-created during import on {now_iso()[:10]}",
                "calculation_method": "fixed",
                "calculation_value": 0.0,
                "base_scheme": "cost_price",
                "active": True,
                "created_at": now_iso(),
            }
            await db.price_schemes.insert_one(new_scheme)
            scheme_keys[sk] = new_scheme["name"]
            schemes_auto_created.append({"key": sk, "name": new_scheme["name"]})

    imported = 0
    skipped = []
    errors = []

    for i, row in enumerate(rows, start=2):  # Row 2 = first data row (row 1 = headers)
        try:
            name_col = col_map.get("name")
            name = _safe_str(row.get(name_col, "")) if name_col else ""
            if not name:
                continue  # Skip blank rows

            # Build product dict from mapping
            sku_col = col_map.get("sku")
            sku_raw = _safe_str(row.get(sku_col, "")) if sku_col else ""
            unit_col = col_map.get("unit")
            unit = (_safe_str(row.get(unit_col, "")) if unit_col else "") or "Piece"
            cat_col = col_map.get("category")
            category = (_safe_str(row.get(cat_col, "")) if cat_col else "") or "General"
            desc_col = col_map.get("description")
            description = _safe_str(row.get(desc_col, "")) if desc_col else ""
            cost_col = col_map.get("cost_price")
            cost_price = _safe_float(row.get(cost_col, 0)) if cost_col else 0.0
            reorder_col = col_map.get("reorder_point")
            reorder_point = _safe_float(row.get(reorder_col, 0)) if reorder_col else 0.0

            type_col = col_map.get("product_type")
            raw_type = (_safe_str(row.get(type_col, "")) if type_col else "").lower()
            if "service" in raw_type:
                product_type = "service"
            else:
                product_type = "stockable"

            # Build prices dict from mapped scheme columns
            prices = {}
            for scheme_key in scheme_keys:
                col_for_scheme = col_map.get(f"{scheme_key}_price", "")
                if col_for_scheme and col_for_scheme in row:
                    val = _safe_float(row.get(col_for_scheme, 0))
                    if val > 0:
                        prices[scheme_key] = val

            # Defensive: also store any *_price column that the user mapped, even if its scheme
            # wasn't auto-created above (e.g. exotic key, single-row imports). Never silently drop.
            for map_key, col in col_map.items():
                if not map_key.endswith("_price") or map_key in ("cost_price", "retail_price"):
                    continue
                if not col or col not in row:
                    continue
                key = map_key[:-len("_price")]
                if key in prices:
                    continue
                val = _safe_float(row.get(col, 0))
                if val > 0:
                    prices[key] = val

            # Handle direct retail_price mapping (QB style)
            if col_map.get("retail_price") and col_map["retail_price"] in row:
                retail = _safe_float(row.get(col_map["retail_price"], 0))
                if retail > 0:
                    prices["retail"] = retail

            # Check for duplicate by name (case-insensitive)
            existing = await db.products.find_one(
                {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}, "active": True},
                {"_id": 0, "id": 1, "name": 1, "sku": 1}
            )
            if existing:
                skipped.append({
                    "row": i,
                    "name": name,
                    "reason": "duplicate_name",
                    "existing_id": existing["id"],
                    "existing_sku": existing.get("sku", ""),
                })
                continue

            # Auto-generate SKU if not provided
            if not sku_raw:
                sku_raw = _generate_sku(name)
            # Ensure SKU uniqueness
            sku_check = await db.products.find_one({"sku": sku_raw, "active": True}, {"_id": 0, "id": 1})
            if sku_check:
                sku_raw = f"{sku_raw[:10]}-{new_id()[:4].upper()}"

            product = {
                "id": new_id(),
                "sku": sku_raw,
                "name": name,
                "category": category,
                "description": description,
                "unit": unit,
                "cost_price": cost_price,
                "prices": prices,
                "parent_id": None,
                "is_repack": False,
                "units_per_parent": None,
                "repack_unit": None,
                "barcode": "",
                "product_type": product_type,
                "capital_method": "manual",
                "reorder_point": reorder_point,
                "reorder_quantity": 0.0,
                "unit_of_measurement": unit,
                "last_vendor": "",
                "active": True,
                "imported_from": "file_import",
                "created_at": now_iso(),
            }
            await db.products.insert_one(product)
            imported += 1

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total_processed": len(rows),
        "schemes_auto_created": schemes_auto_created,
        "summary": f"Imported {imported} products. {len(skipped)} skipped (duplicates). {len(errors)} errors.",
    }


@router.post("/products/overwrite")
async def overwrite_products(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    product_ids: str = Form(""),   # JSON array of existing product IDs to overwrite
    user=Depends(get_current_user),
):
    """
    Merge-overwrite existing products from the source file.

    For each row whose Product Name matches one of the selected product_ids,
    only the *mapped* fields are written. The `prices` map is MERGED — existing
    scheme keys (e.g. retail) are preserved unless the user mapped a column for
    them. This lets users add wholesale prices to 1000s of products without
    losing their existing retail prices.
    """
    check_perm(user, "products", "edit")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
        ids_to_overwrite = set(json.loads(product_ids) if product_ids else [])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON for mapping or product_ids")

    if not ids_to_overwrite:
        raise HTTPException(status_code=400, detail="No products selected to overwrite")
    if not col_map.get("name"):
        raise HTTPException(status_code=400, detail="Column mapping must include 'name'")

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Look up the selected products and index by name (case-insensitive)
    targets = await db.products.find(
        {"id": {"$in": list(ids_to_overwrite)}, "active": True},
        {"_id": 0},
    ).to_list(len(ids_to_overwrite))
    name_to_product = {p["name"].lower(): p for p in targets}

    # Discover & auto-create any missing schemes from mapped *_price columns
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0}).to_list(50)
    scheme_keys = {s["key"]: s["name"] for s in schemes}

    mapped_scheme_keys = set()
    for map_key in col_map:
        if map_key.endswith("_price") and map_key != "cost_price" and col_map.get(map_key):
            mapped_scheme_keys.add(map_key[: -len("_price")])

    schemes_auto_created = []
    for sk in mapped_scheme_keys:
        if sk and sk not in scheme_keys:
            new_scheme = {
                "id": new_id(),
                "name": sk.replace("_", " ").title(),
                "key": sk,
                "description": f"Auto-created during overwrite on {now_iso()[:10]}",
                "calculation_method": "fixed",
                "calculation_value": 0.0,
                "base_scheme": "cost_price",
                "active": True,
                "created_at": now_iso(),
            }
            await db.price_schemes.insert_one(new_scheme)
            scheme_keys[sk] = new_scheme["name"]
            schemes_auto_created.append({"key": sk, "name": new_scheme["name"]})

    updated = 0
    not_matched = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            name = _safe_str(row.get(col_map["name"], ""))
            if not name:
                continue
            target = name_to_product.get(name.lower())
            if not target:
                not_matched += 1
                continue

            update = {}

            # Scalar field merges — only when user mapped the column
            if col_map.get("sku") and col_map["sku"] in row:
                v = _safe_str(row.get(col_map["sku"], ""))
                if v:
                    update["sku"] = v
            if col_map.get("unit") and col_map["unit"] in row:
                v = _safe_str(row.get(col_map["unit"], ""))
                if v:
                    update["unit"] = v
                    update["unit_of_measurement"] = v
            if col_map.get("category") and col_map["category"] in row:
                v = _safe_str(row.get(col_map["category"], ""))
                if v:
                    update["category"] = v
            if col_map.get("description") and col_map["description"] in row:
                update["description"] = _safe_str(row.get(col_map["description"], ""))
            if col_map.get("cost_price") and col_map["cost_price"] in row:
                v = _safe_float(row.get(col_map["cost_price"], 0))
                if v > 0:
                    update["cost_price"] = v
            if col_map.get("reorder_point") and col_map["reorder_point"] in row:
                update["reorder_point"] = _safe_float(row.get(col_map["reorder_point"], 0))
            if col_map.get("product_type") and col_map["product_type"] in row:
                raw_type = _safe_str(row.get(col_map["product_type"], "")).lower()
                if "service" in raw_type:
                    update["product_type"] = "service"
                elif raw_type:
                    update["product_type"] = "stockable"

            # MERGE prices: start from existing, layer in any mapped scheme prices
            new_prices = dict(target.get("prices") or {})
            prices_changed = False

            if col_map.get("retail_price") and col_map["retail_price"] in row:
                v = _safe_float(row.get(col_map["retail_price"], 0))
                if v > 0 and new_prices.get("retail") != v:
                    new_prices["retail"] = v
                    prices_changed = True

            for scheme_key in scheme_keys:
                col = col_map.get(f"{scheme_key}_price", "")
                if col and col in row:
                    v = _safe_float(row.get(col, 0))
                    if v > 0 and new_prices.get(scheme_key) != v:
                        new_prices[scheme_key] = v
                        prices_changed = True

            # Defensive: any other *_price column the user mapped
            for map_key, col in col_map.items():
                if not map_key.endswith("_price") or map_key in ("cost_price", "retail_price"):
                    continue
                if not col or col not in row:
                    continue
                key = map_key[: -len("_price")]
                v = _safe_float(row.get(col, 0))
                if v > 0 and new_prices.get(key) != v:
                    new_prices[key] = v
                    prices_changed = True

            if prices_changed:
                update["prices"] = new_prices

            if not update:
                continue
            update["updated_at"] = now_iso()
            await db.products.update_one({"id": target["id"]}, {"$set": update})
            updated += 1

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "updated": updated,
        "not_matched": not_matched,
        "errors": errors,
        "schemes_auto_created": schemes_auto_created,
        "summary": f"Merged {updated} products from file. {not_matched} rows did not match any selected product. {len(errors)} errors.",
    }


@router.post("/inventory-seed")
async def import_inventory_seed(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    branch_id: str = Form(""),
    pin: str = Form(""),
    user=Depends(get_current_user)
):
    """
    Set initial inventory quantities for a branch from a file upload.
    Requires admin user or manager PIN.
    Matches products by name (case-insensitive). Unmatched → report.
    """
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required for inventory seed")

    # PIN check for non-admin users
    if user.get("role") != "admin":
        if not pin:
            raise HTTPException(status_code=403, detail="Admin PIN required for inventory seed")
        # Verify against owner account
        owner = await db.users.find_one({"role": "admin"}, {"_id": 0})
        if not owner or not verify_password(pin, owner.get("password_hash", "")):
            raise HTTPException(status_code=403, detail="Invalid PIN")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid column mapping JSON")

    if not col_map.get("name") or not col_map.get("quantity"):
        raise HTTPException(status_code=400, detail="Mapping must include 'name' and 'quantity' fields")

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    updated = 0
    not_found = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            name = _safe_str(row.get(col_map.get("name", ""), ""))
            qty = _safe_float(row.get(col_map.get("quantity", ""), 0))

            if not name:
                continue

            # Match product by name (exact, case-insensitive)
            product = await db.products.find_one(
                {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}, "active": True},
                {"_id": 0, "id": 1, "name": 1}
            )
            if not product:
                not_found.append({"row": i, "name": name, "quantity": qty})
                continue

            # Set (not add) inventory quantity
            existing = await db.inventory.find_one(
                {"product_id": product["id"], "branch_id": branch_id}, {"_id": 0}
            )
            if existing:
                await db.inventory.update_one(
                    {"product_id": product["id"], "branch_id": branch_id},
                    {"$set": {"quantity": qty, "updated_at": now_iso()}}
                )
            else:
                await db.inventory.insert_one({
                    "id": new_id(),
                    "product_id": product["id"],
                    "branch_id": branch_id,
                    "quantity": qty,
                    "updated_at": now_iso(),
                })
            updated += 1

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "updated": updated,
        "not_found": not_found,
        "errors": errors,
        "total_processed": len(rows),
        "summary": f"Set inventory for {updated} products. {len(not_found)} not found. {len(errors)} errors.",
    }


@router.get("/template/{template_type}")
async def download_template(template_type: str, user=Depends(get_current_user)):
    """Download a CSV template for the specified import type."""
    if template_type == "products":
        headers = ["Product Name", "SKU", "Category", "Unit", "Description", "Type",
                   "Retail Price", "Wholesale Price", "Cost Price", "Reorder Point"]
        sample = [["Sample Product A", "PROD-001", "Feeds", "BAG", "50kg bag", "stockable",
                   "1500", "1350", "1200", "10"],
                  ["Sample Product B", "", "Pesticide", "BOTTLE", "", "stockable",
                   "250", "225", "200", "5"]]
    elif template_type == "inventory-seed":
        headers = ["Product Name", "Quantity"]
        sample = [["Sample Product A", "50"],
                  ["Sample Product B", "120"]]
    else:
        raise HTTPException(status_code=404, detail="Unknown template type")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(sample)
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=agripos_{template_type}_template.csv"}
    )
