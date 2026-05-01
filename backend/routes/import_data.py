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
from difflib import SequenceMatcher
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id, verify_password,
    generate_next_number, mark_price_reviewed,
)

router = APIRouter(prefix="/import", tags=["Import"])


# =============================================================================
# Customer name normalization & fuzzy matching helpers
# =============================================================================
_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _norm_name(s: str) -> str:
    """Lowercase, strip punctuation, collapse spaces — for stable matching/dedup."""
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def _token_sort(s: str) -> str:
    """Sort the words alphabetically — handles 'Juan Dela Cruz' vs 'Dela Cruz Juan'."""
    return " ".join(sorted(_norm_name(s).split()))


def _phone_tail(p: str, n: int = 9) -> str:
    """Last n digits of a phone number — handles country-code differences."""
    digits = re.sub(r"\D", "", p or "")
    return digits[-n:] if len(digits) >= n else digits


def _name_similar(a: str, b: str) -> float:
    """0..1 similarity using difflib on token-sorted normalized names."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _token_sort(a), _token_sort(b)).ratio()


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


@router.post("/products/update-existing")
async def update_existing_products(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    user=Depends(get_current_user),
):
    """
    Update Existing Products mode — match every row by Product Name and merge
    only the mapped fields. Rows that don't match any existing product are
    reported (NOT created). Use this for bulk migration cleanups where you
    just want to update prices/SKUs/cost on existing data.
    """
    check_perm(user, "products", "edit")

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

    # Schemes — auto-create from any mapped *_price columns
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
                "description": f"Auto-created during update-existing import on {now_iso()[:10]}",
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
    not_found = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            name = _safe_str(row.get(col_map["name"], ""))
            if not name:
                continue

            existing = await db.products.find_one(
                {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}, "active": True},
                {"_id": 0},
            )
            if not existing:
                not_found.append({"row": i, "name": name})
                continue

            update = {}
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

            # MERGE prices map
            new_prices = dict(existing.get("prices") or {})
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
            await db.products.update_one({"id": existing["id"]}, {"$set": update})
            updated += 1

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "updated": updated,
        "not_found": not_found,
        "errors": errors,
        "total_processed": len(rows),
        "schemes_auto_created": schemes_auto_created,
        "summary": f"Updated {updated} existing products. {len(not_found)} not found in system. {len(errors)} errors.",
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
    """Download a CSV template for the specified import type.

    For product / branch-stock-and-price templates, the price-scheme columns
    are sourced live from `price_schemes` so a tenant who's added a "Credit"
    scheme gets a "Credit Price" column in their downloaded template — no
    code change needed when new schemes are introduced.
    """
    # Resolve active price-scheme display columns once. We special-case
    # cost/quantity (separate columns) and skip any scheme whose key would
    # collide with non-price reserved columns. Order = API order so the CSV
    # matches the on-screen mapper.
    scheme_docs = await db.price_schemes.find(
        {"active": True}, {"_id": 0, "key": 1, "name": 1}
    ).to_list(50)
    reserved = {"cost", "quantity", "name", "sku"}
    active_schemes = [s for s in (scheme_docs or []) if s.get("key") and s["key"] not in reserved]
    if not active_schemes:
        # Fallback for fresh tenants — keeps the template usable.
        active_schemes = [{"key": "retail", "name": "Retail"},
                          {"key": "wholesale", "name": "Wholesale"}]
    scheme_cols = [f"{s['name']} Price" for s in active_schemes]

    if template_type == "products":
        headers = ["Product Name", "SKU", "Category", "Unit", "Description", "Type",
                   *scheme_cols, "Cost Price", "Reorder Point"]
        # Sample row — derive a plausible example for each scheme so the
        # admin sees the expected format. Retail >= Wholesale >= Cost is the
        # convention; new schemes get a placeholder midway.
        scheme_sample_a = ["1500" if s["key"] == "retail"
                           else "1350" if s["key"] == "wholesale"
                           else "1400"
                           for s in active_schemes]
        scheme_sample_b = ["250" if s["key"] == "retail"
                           else "225" if s["key"] == "wholesale"
                           else "235"
                           for s in active_schemes]
        sample = [["Sample Product A", "PROD-001", "Feeds", "BAG", "50kg bag", "stockable",
                   *scheme_sample_a, "1200", "10"],
                  ["Sample Product B", "", "Pesticide", "BOTTLE", "", "stockable",
                   *scheme_sample_b, "200", "5"]]
    elif template_type == "inventory-seed":
        headers = ["Product Name", "Quantity"]
        sample = [["Sample Product A", "50"],
                  ["Sample Product B", "120"]]
    elif template_type == "branch-stock-and-price":
        headers = ["Product Name", "Cost Price", *scheme_cols, "Quantity"]
        scheme_sample_a = ["1500" if s["key"] == "retail"
                           else "1350" if s["key"] == "wholesale"
                           else "1400"
                           for s in active_schemes]
        scheme_sample_b = ["260" if s["key"] == "retail"
                           else "230" if s["key"] == "wholesale"
                           else "245"
                           for s in active_schemes]
        sample = [["Sample Product A", "1180", *scheme_sample_a, "40"],
                  ["Sample Product B", "195",  *scheme_sample_b, ""]]
    elif template_type == "customers":
        headers = ["Customer Name", "Phone", "Phone 2", "Email", "Address",
                   "Price Scheme", "Credit Limit", "Interest Rate",
                   "Grace Period (days)", "Opening Balance"]
        sample = [["Juan Dela Cruz", "09171234567", "", "juan@example.com",
                   "Brgy. San Jose, Roxas", "retail", "5000", "2", "7", "1250"],
                  ["Maria Santos", "09229876543", "", "", "Brgy. Bagong Sikat",
                   "wholesale", "20000", "0", "15", "0"]]
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


# =============================================================================
# BRANCH-SPECIFIC STOCK + PRICE IMPORT
# =============================================================================
# Uploads stock + price *for one branch only*. Writes to:
#   - branch_prices  (per-branch price overrides)
#   - inventory      (per-branch quantities)
# Never touches the global product.prices map. Main branch is always safe.
#
# Cell rules:
#   - Empty price cell → SKIPPED (existing override / global fallback preserved)
#   - Empty quantity cell → set to 0 (branch does not carry the product)


async def _resolve_price_schemes_for_import(col_map: dict) -> tuple[dict, list]:
    """
    Read existing price schemes and auto-create any user-mapped *_price scheme
    that doesn't yet exist. Returns ({scheme_key: name}, [auto_created]).
    """
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0}).to_list(50)
    scheme_keys = {s["key"]: s["name"] for s in schemes}
    mapped = set()
    for k in col_map:
        if k.endswith("_price") and k != "cost_price" and col_map.get(k):
            mapped.add(k[: -len("_price")])
    auto = []
    for sk in mapped:
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
            auto.append({"key": sk, "name": new_scheme["name"]})
    return scheme_keys, auto


@router.post("/branch-stock-and-price")
async def import_branch_stock_and_price(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    branch_id: str = Form(""),
    pin: str = Form(""),
    mode: str = Form("preview"),    # "preview" or "commit"
    user=Depends(get_current_user),
):
    """
    Branch-scoped stock + price import. Matches by Product Name against the
    GLOBAL catalog. Writes per-branch overrides only.
    """
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    # PIN required for non-admin (same policy as inventory-seed)
    if user.get("role") != "admin":
        if not pin:
            raise HTTPException(status_code=403, detail="Admin PIN required")
        owner = await db.users.find_one({"role": "admin"}, {"_id": 0})
        if not owner or not verify_password(pin, owner.get("password_hash", "")):
            raise HTTPException(status_code=403, detail="Invalid PIN")

    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "id": 1, "name": 1})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid column mapping JSON")

    if not col_map.get("name"):
        raise HTTPException(status_code=400, detail="Mapping must include 'name' field")

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    scheme_keys, schemes_auto_created = await _resolve_price_schemes_for_import(col_map)

    name_col = col_map["name"]
    qty_col = col_map.get("quantity", "")
    cost_col = col_map.get("cost_price", "")

    matched = []           # rows that resolve to a product
    unmatched = []         # rows where name didn't match any product
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            name = _safe_str(row.get(name_col, ""))
            if not name:
                continue

            product = await db.products.find_one(
                {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}, "active": True},
                {"_id": 0, "id": 1, "name": 1, "sku": 1, "prices": 1, "cost_price": 1},
            )
            if not product:
                unmatched.append({"row": i, "name": name})
                continue

            # Build price overrides — empty cells SKIPPED
            new_prices = {}
            if col_map.get("retail_price") and col_map["retail_price"] in row:
                v = row.get(col_map["retail_price"], "")
                if v not in (None, "", " "):
                    new_prices["retail"] = _safe_float(v)
            for sk in scheme_keys:
                if sk == "retail":
                    continue
                col = col_map.get(f"{sk}_price", "")
                if col and col in row:
                    v = row.get(col, "")
                    if v not in (None, "", " "):
                        new_prices[sk] = _safe_float(v)

            # Cost — empty SKIPPED
            new_cost = None
            if cost_col and cost_col in row:
                v = row.get(cost_col, "")
                if v not in (None, "", " "):
                    new_cost = _safe_float(v)

            # Quantity — empty SKIPPED (treat empty as "no info", not as zero;
            # writing 0 to a row with a blank/un-cached cell is too risky).
            # If you truly want zero stock, type 0 explicitly.
            new_qty = None
            if qty_col and qty_col in row:
                v = row.get(qty_col, "")
                if v not in (None, "", " "):
                    new_qty = _safe_float(v)

            matched.append({
                "row": i,
                "name": name,
                "product_id": product["id"],
                "sku": product.get("sku", ""),
                "global_prices": product.get("prices") or {},
                "global_cost": product.get("cost_price", 0),
                "new_prices": new_prices,
                "new_cost": new_cost,
                "new_qty": new_qty,
            })
        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(name_col, "")), "error": str(e)})

    # Preview mode → return matched/unmatched without writing
    if mode != "commit":
        return {
            "mode": "preview",
            "branch": branch,
            "matched": matched[:200],   # cap preview size
            "matched_count": len(matched),
            "unmatched": unmatched,
            "errors": errors,
            "schemes_auto_created": schemes_auto_created,
            "summary": (
                f"{len(matched)} products will update, {len(unmatched)} not found, "
                f"{len(errors)} errors. Branch: {branch.get('name', '')}."
            ),
        }

    # Commit mode → write
    prices_updated = 0
    qty_updated = 0
    for m in matched:
        wrote_something = False
        try:
            # Branch prices upsert (only if there's something to write)
            if m["new_prices"] or m["new_cost"] is not None:
                existing = await db.branch_prices.find_one(
                    {"product_id": m["product_id"], "branch_id": branch_id}, {"_id": 0}
                )
                merged_prices = dict((existing or {}).get("prices") or {})
                merged_prices.update(m["new_prices"])

                doc = {
                    "product_id": m["product_id"],
                    "branch_id": branch_id,
                    "prices": merged_prices,
                    "updated_at": now_iso(),
                    "updated_by_id": user["id"],
                    "updated_by_name": user.get("full_name", user["username"]),
                }
                if m["new_cost"] is not None:
                    doc["cost_price"] = m["new_cost"]

                if existing:
                    await db.branch_prices.update_one(
                        {"product_id": m["product_id"], "branch_id": branch_id},
                        {"$set": doc},
                    )
                else:
                    doc["id"] = new_id()
                    doc["created_at"] = now_iso()
                    await db.branch_prices.insert_one(doc)
                prices_updated += 1
                wrote_something = True

            # Inventory set (empty = 0)
            if m["new_qty"] is not None:
                inv_existing = await db.inventory.find_one(
                    {"product_id": m["product_id"], "branch_id": branch_id}, {"_id": 0}
                )
                if inv_existing:
                    await db.inventory.update_one(
                        {"product_id": m["product_id"], "branch_id": branch_id},
                        {"$set": {"quantity": m["new_qty"], "updated_at": now_iso()}},
                    )
                else:
                    await db.inventory.insert_one({
                        "id": new_id(),
                        "product_id": m["product_id"],
                        "branch_id": branch_id,
                        "quantity": m["new_qty"],
                        "updated_at": now_iso(),
                    })
                qty_updated += 1
                wrote_something = True
        except Exception as e:
            errors.append({"row": m["row"], "name": m["name"], "error": str(e)})

        # Only mark reviewed if we actually wrote something — rows with all
        # empty cells should be left untouched (no implicit ack).
        if wrote_something:
            try:
                await mark_price_reviewed(m["product_id"], branch_id, source="import")
            except Exception:
                pass

    return {
        "mode": "commit",
        "branch": branch,
        "prices_updated": prices_updated,
        "qty_updated": qty_updated,
        "unmatched": unmatched,
        "errors": errors,
        "schemes_auto_created": schemes_auto_created,
        "summary": (
            f"Committed: {prices_updated} price overrides and {qty_updated} stock entries "
            f"for {branch.get('name', '')}. {len(unmatched)} not found. {len(errors)} errors."
        ),
    }


# =============================================================================
# CUSTOMER IMPORT (branch-scoped + smart duplicate detection + opening balance)
# =============================================================================

# Similarity threshold: token-sorted SequenceMatcher ratio. ~0.85 corresponds
# roughly to Levenshtein distance ≤ 2 on short names.
_FUZZY_THRESHOLD = 0.85


def _customer_payload_from_row(row: dict, col_map: dict) -> dict:
    """Build a normalized customer-payload dict from a CSV row + column mapping."""
    def s(k):
        c = col_map.get(k, "")
        return _safe_str(row.get(c, "")) if c else ""
    def f(k, default=0.0):
        c = col_map.get(k, "")
        return _safe_float(row.get(c, default)) if c else default

    phones = []
    for k in ("phone", "phone2"):
        v = s(k)
        if v:
            phones.append(v)

    return {
        "name": s("name"),
        "phones": phones,
        "email": s("email"),
        "address": s("address"),
        "price_scheme": (s("price_scheme") or "retail").lower(),
        "credit_limit": f("credit_limit"),
        "interest_rate": f("interest_rate"),
        "grace_period": int(f("grace_period", 7) or 7),
        "opening_balance": f("opening_balance"),
    }


async def _find_customer_dupes(payload: dict, branch_id: str, candidates: list[dict]) -> dict:
    """
    Classify a payload against existing customers in this branch.
    Returns: {"verdict": "exact"|"fuzzy"|"none", "matches": [{...}]}
    """
    name = payload["name"]
    phones = payload["phones"]
    norm_name = _norm_name(name)
    phone_tails = {_phone_tail(p) for p in phones if p}
    phone_tails.discard("")

    # 1) Exact: same normalized name OR exact phone match
    for c in candidates:
        if _norm_name(c.get("name", "")) == norm_name and norm_name:
            return {"verdict": "exact", "matches": [
                {"id": c["id"], "name": c["name"], "phone": c.get("phone", ""), "reason": "name"}
            ]}
        c_phones = c.get("phones") or ([c["phone"]] if c.get("phone") else [])
        for cp in c_phones:
            if cp and _phone_tail(cp) in phone_tails:
                return {"verdict": "exact", "matches": [
                    {"id": c["id"], "name": c["name"], "phone": cp, "reason": "phone"}
                ]}

    # 2) Fuzzy: token-sort similarity ≥ threshold OR partial phone tail
    fuzzy = []
    for c in candidates:
        sim = _name_similar(name, c.get("name", ""))
        if sim >= _FUZZY_THRESHOLD:
            fuzzy.append({
                "id": c["id"], "name": c["name"], "phone": c.get("phone", ""),
                "similarity": round(sim, 3), "reason": "name_similar",
            })
            continue
        c_phones = c.get("phones") or ([c["phone"]] if c.get("phone") else [])
        for cp in c_phones:
            if cp and _phone_tail(cp) and _phone_tail(cp) in phone_tails:
                fuzzy.append({
                    "id": c["id"], "name": c["name"], "phone": cp,
                    "similarity": round(sim, 3), "reason": "phone_partial",
                })
                break
    if fuzzy:
        # Sort best match first, return up to 3 candidates
        fuzzy.sort(key=lambda x: x["similarity"], reverse=True)
        return {"verdict": "fuzzy", "matches": fuzzy[:3]}

    return {"verdict": "none", "matches": []}


@router.post("/customers/preview")
async def preview_customer_import(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    branch_id: str = Form(""),
    user=Depends(get_current_user),
):
    """
    Analyze customer file. Every row is treated as a new customer — NO duplicate
    blocking at import time. Duplicates are reviewed AFTER import via the
    Customer Dedupe tool (pop-up like Smart Price Checker).

    Returns:
    - auto_create: rows ready to import (always includes every valid row)
    - errors: rows with parsing problems
    - total_opening_balance: sum of opening balances that will be invoiced
    - existing_similar_count: how many existing customers would later be flagged
      as possible duplicates (informational only — does NOT block import).
    """
    check_perm(user, "customers", "create")

    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid column mapping JSON")

    if not col_map.get("name"):
        raise HTTPException(status_code=400, detail="Mapping must include 'name' field")

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Load existing active customers (informational — for existing_similar_count only)
    candidates = await db.customers.find(
        {"branch_id": branch_id, "active": True},
        {"_id": 0, "id": 1, "name": 1, "phone": 1, "phones": 1},
    ).to_list(20000)

    auto_create = []
    errors = []
    total_opening_balance = 0.0
    rows_with_similar_existing = 0

    for i, row in enumerate(rows, start=2):
        try:
            payload = _customer_payload_from_row(row, col_map)
            if not payload["name"]:
                continue

            auto_create.append({"row": i, "payload": payload})
            ob = float(payload.get("opening_balance") or 0)
            if ob > 0:
                total_opening_balance += ob

            # Count (don't block) if this row looks like an existing customer
            classification = await _find_customer_dupes(payload, branch_id, candidates)
            if classification["verdict"] in ("exact", "fuzzy"):
                rows_with_similar_existing += 1

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "branch_id": branch_id,
        "auto_create": auto_create,
        # Back-compat — frontend may still read these, always empty now
        "exact_dupe": [],
        "fuzzy": [],
        "errors": errors,
        "total_opening_balance": round(total_opening_balance, 2),
        "existing_similar_count": rows_with_similar_existing,
        "summary": (
            f"{len(auto_create)} customers will be imported. "
            f"Opening balances total ₱{total_opening_balance:,.2f}. "
            f"{rows_with_similar_existing} rows resemble existing customers — "
            f"you can merge them afterwards via the Duplicate Review popup. "
            f"{len(errors)} parse errors."
        ),
    }


async def _create_opening_balance_invoice(
    customer: dict, opening_balance: float, branch_id: str,
    user: dict, ob_date: str,
) -> dict:
    """
    Create a single 'Opening Balance Carry-forward' invoice flagged as such.
    Flows through the same path as a credit sale so it appears in:
      - AR aging / receivables
      - Customer ledger
      - Closing wizard (under credit sales)
    Tagged is_opening_balance=True so reports can filter it out of real sales.
    """
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = (settings or {}).get("value", {}).get("sales_invoice", "SI") if settings else "SI"
    inv_number = await generate_next_number(prefix, branch_id)

    items = [{
        "product_id": "",
        "product_name": "Opening Balance Carry-forward (Migrated)",
        "description": "Imported opening balance from prior system",
        "quantity": 1,
        "rate": opening_balance,
        "discount_type": "amount",
        "discount_value": 0,
        "discount_amount": 0,
        "total": opening_balance,
        "is_repack": False,
    }]

    invoice = {
        "id": new_id(),
        "invoice_number": inv_number,
        "prefix": prefix,
        "customer_id": customer["id"],
        "customer_name": customer.get("name", ""),
        "customer_contact": customer.get("name", ""),
        "customer_phone": customer.get("phone", ""),
        "customer_address": customer.get("address", ""),
        "terms": "Net 30",
        "terms_days": 30,
        "customer_po": "",
        "sales_rep_id": user["id"],
        "sales_rep_name": user.get("full_name", user["username"]),
        "branch_id": branch_id,
        "order_date": ob_date,
        "invoice_date": ob_date,
        "due_date": ob_date,
        "items": items,
        "subtotal": opening_balance,
        "freight": 0,
        "overall_discount": 0,
        "grand_total": opening_balance,
        "amount_paid": 0,
        "balance": opening_balance,
        "interest_rate": float(customer.get("interest_rate", 0)),
        "interest_accrued": 0,
        "penalties": 0,
        "last_interest_date": None,
        "sale_type": "walk_in",
        "payment_type": "credit",
        "payment_method": "Credit",
        "fund_source": "cashier",
        "status": "open",
        "payments": [],
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name", user["username"]),
        "is_opening_balance": True,
        "imported_from": "customer_import",
        "created_at": now_iso(),
    }
    await db.invoices.insert_one(invoice)
    invoice.pop("_id", None)
    return invoice


async def _send_opening_balance_sms(customer: dict, amount: float, branch_id: str) -> int:
    """
    Queue a one-time opening_balance_notice SMS to every phone on file.
    Idempotent via dedup_key tied to (customer_id, opening_balance).
    Returns number of SMS queued.
    """
    try:
        from routes.sms import queue_sms, _ensure_templates
        from routes.sms_hooks import get_company_name, get_branch_name, _resolve_org_id
    except Exception:
        return 0

    phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
    phones = [p for p in phones if p]
    if not phones:
        return 0

    # Make sure the new opening_balance_notice template exists for this tenant
    # (idempotent — only fills missing keys, never overwrites customizations).
    try:
        await _ensure_templates()
    except Exception:
        pass

    org_id = await _resolve_org_id(branch_id)
    company_name = await get_company_name(org_id)
    branch_name = await get_branch_name(branch_id)

    variables = {
        "customer_name": customer.get("name", ""),
        "amount": f"{amount:,.2f}",
        "company_name": company_name,
        "branch_name": branch_name,
        "date": now_iso()[:10],
    }
    queued = 0
    for phone in phones:
        await queue_sms(
            template_key="opening_balance_notice",
            customer_id=customer["id"],
            customer_name=customer.get("name", ""),
            phone=phone,
            variables=variables,
            organization_id=org_id,
            branch_id=branch_id,
            branch_name=branch_name,
            trigger="auto",
            trigger_ref=customer["id"],
            dedup_key=f"opening_balance_notice:{customer['id']}:{phone}",
        )
        queued += 1
    return queued


@router.post("/customers/commit")
async def commit_customer_import(
    file: UploadFile = File(...),
    mapping: str = Form(""),
    branch_id: str = Form(""),
    decisions: str = Form("[]"),       # kept for backward compat; ignored
    opening_balance_date: str = Form(""),  # YYYY-MM-DD; defaults to today
    user=Depends(get_current_user),
):
    """
    Commit the customer import. Every valid row becomes a new customer (plus
    an opening-balance invoice when OB > 0). Duplicate detection happens
    AFTER import via the Customer Dedupe pop-up — this keeps import simple
    and ensures no opening balance ever gets silently dropped.

    The `decisions` form field is accepted but ignored (kept for compat with
    older frontends during rollout).
    """
    check_perm(user, "customers", "create")

    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    import json
    try:
        col_map = json.loads(mapping) if mapping else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in mapping")

    if not col_map.get("name"):
        raise HTTPException(status_code=400, detail="Mapping must include 'name' field")

    ob_date = opening_balance_date or now_iso()[:10]

    try:
        content = await file.read()
        rows = _read_file(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    created = 0
    skipped = []
    invoiced = []         # rows where opening balance invoice was created
    ob_amount_total = 0.0
    sms_queued = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            payload = _customer_payload_from_row(row, col_map)
            if not payload["name"]:
                skipped.append({"row": i, "name": "", "reason": "empty_name"})
                continue

            # Create new customer (NO duplicate blocking — handled post-import)
            phone_primary = payload["phones"][0] if payload["phones"] else ""
            new_customer = {
                "id": new_id(),
                "name": payload["name"],
                "phone": phone_primary,
                "phones": payload["phones"],
                "email": payload["email"],
                "address": payload["address"],
                "price_scheme": payload["price_scheme"] or "retail",
                "credit_limit": payload["credit_limit"],
                "interest_rate": payload["interest_rate"],
                "grace_period": payload["grace_period"],
                "balance": 0.0,
                "branch_id": branch_id,
                "active": True,
                "imported_from": "customer_import",
                "created_at": now_iso(),
            }
            await db.customers.insert_one(new_customer)
            new_customer.pop("_id", None)
            created += 1

            # Opening balance invoice + SMS
            ob = float(payload.get("opening_balance") or 0)
            if ob > 0:
                inv = await _create_opening_balance_invoice(
                    new_customer, ob, branch_id, user, ob_date,
                )
                invoiced.append({
                    "row": i,
                    "customer_id": new_customer["id"],
                    "customer_name": new_customer["name"],
                    "invoice_number": inv["invoice_number"],
                    "amount": ob,
                })
                ob_amount_total += ob
                # Balance = the single OB invoice (no other invoices yet)
                await db.customers.update_one(
                    {"id": new_customer["id"]},
                    {"$set": {"balance": ob, "updated_at": now_iso()}},
                )
                sms_queued += await _send_opening_balance_sms(new_customer, ob, branch_id)

        except Exception as e:
            errors.append({"row": i, "name": _safe_str(row.get(col_map.get("name", ""), "")), "error": str(e)})

    return {
        "created": created,
        "merged": 0,  # no merges at import time — done post-import via dedupe tool
        "skipped": skipped,
        "invoiced_count": len(invoiced),
        "invoiced": invoiced[:200],
        "ob_amount_total": round(ob_amount_total, 2),
        "sms_queued": sms_queued,
        "errors": errors,
        "summary": (
            f"Created {created} customers, created {len(invoiced)} opening-balance "
            f"invoices totalling ₱{ob_amount_total:,.2f}, queued {sms_queued} SMS, "
            f"{len(errors)} errors. Review possible duplicates in the 'Duplicate Review' pop-up."
        ),
    }

