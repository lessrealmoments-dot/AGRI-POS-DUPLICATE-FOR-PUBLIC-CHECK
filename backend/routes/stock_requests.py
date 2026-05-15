"""
Stock Requests — the *initiation* document for inter-branch fulfillment.

Workflow (Feb 2026):
    Branch A drafts a request listing what they need. Branch B (the
    supplying branch) opens the request, triages each line into either
        • Personal Transfer  → spawns 1 BTO from existing flow
        • Supplier PO        → spawns N DRAFT POs grouped by supplier,
                               sitting on Branch A's books with a
                               phantom mirror on Branch B's list.

Key design choices:
  * Stock-request docs are *not* inventory-bearing. Triage spawns the
    real BTO/POs and the request acts purely as the audit anchor.
  * One request line ↔ exactly one fulfillment type (no splits in v1).
  * Suppliers belong to the requesting branch's directory (PO lives
    there). Quick-create writes to the requesting branch's suppliers.
  * Phantom POs are real `purchase_orders` rows with
    `phantom_for_branch_id = supplying_branch_id` so the supplying
    branch can track them without ever owning inventory or AP.

Endpoints:
    POST   /stock-requests                          create (Branch A)
    GET    /stock-requests                          list w/ filters
    GET    /stock-requests/{id}                     detail incl. child docs
    POST   /stock-requests/{id}/send                draft → sent
    POST   /stock-requests/{id}/triage              spawn BTO + draft POs
    POST   /stock-requests/{id}/cancel              cancel (cascades to draft POs)
    POST   /stock-requests/{id}/po/{po_id}/mark-ordered
                                                    Branch B: phantom PO
                                                    draft→ordered + SMS to A
    GET    /stock-requests/products-lookup          shared product search w/
                                                    both branches' inventory
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone

from config import db, _raw_db, get_org_context
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    ensure_org_context, assert_branch_access,
    generate_next_number,
)

router = APIRouter(prefix="/stock-requests", tags=["Stock Requests"])


# ── Helpers ─────────────────────────────────────────────────────────────────
async def _gen_request_number(branch_id: str) -> str:
    """Format: SR-YYYYMMDD-####  (per-day per-branch sequence)."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    count = await db.stock_requests.count_documents({
        "request_number": {"$regex": f"^SR-{today}-"}
    })
    return f"SR-{today}-{str(count + 1).zfill(4)}"


def _validate_items(items: list) -> list:
    """Light validation + normalisation. Each item:
        product_id, product_name, qty, unit (optional), notes (optional)"""
    if not items:
        raise HTTPException(400, "Request must contain at least one line.")
    out = []
    for raw in items:
        pid = (raw.get("product_id") or "").strip()
        name = (raw.get("product_name") or "").strip()
        qty = float(raw.get("qty") or 0)
        if not pid or not name:
            raise HTTPException(400, "Each line needs product_id and product_name.")
        if qty <= 0:
            raise HTTPException(400, f"Quantity must be > 0 for {name}.")
        out.append({
            "id":              new_id(),
            "product_id":      pid,
            "product_name":    name,
            "qty":             qty,
            "unit":            (raw.get("unit") or "").strip(),
            "notes":           (raw.get("notes") or "").strip(),
            # triage fields — filled in by Branch B at triage time
            "fulfillment_type":   None,   # 'transfer' | 'supplier_po' | 'unfulfilled'
            "supplier_id":        None,
            "supplier_name":      None,
            "assigned_doc_id":    None,   # BTO id or PO id
            "assigned_doc_type":  None,   # 'bto' | 'po'
            "status":             "pending",  # pending | assigned | received | variance
        })
    return out


# ── Create ──────────────────────────────────────────────────────────────────
@router.post("")
async def create_request(data: dict, user=Depends(get_current_user)):
    """Branch A creates a stock request targeting Branch B.

    Body: {requesting_branch_id, supplying_branch_id, items, notes?}
    """
    check_perm(user, "branch_transfers", "create")  # mirror BTO permission

    req_bid = (data.get("requesting_branch_id") or "").strip()
    sup_bid = (data.get("supplying_branch_id") or "").strip()
    if not req_bid or not sup_bid:
        raise HTTPException(400, "requesting_branch_id and supplying_branch_id required.")
    if req_bid == sup_bid:
        raise HTTPException(400, "Requesting and supplying branches cannot be the same.")

    assert_branch_access(user, req_bid)
    if not get_org_context():
        await ensure_org_context(branch_id=req_bid)

    items = _validate_items(data.get("items", []))

    request_number = await _gen_request_number(req_bid)
    doc = {
        "id":                    new_id(),
        "request_number":        request_number,
        "requesting_branch_id":  req_bid,
        "supplying_branch_id":   sup_bid,
        "status":                "draft",
        "items":                 items,
        "notes":                 (data.get("notes") or "").strip(),
        "created_by":            user["id"],
        "created_by_name":       user.get("full_name") or user.get("username", ""),
        "created_at":            now_iso(),
        "sent_at":               None,
        "triaged_at":            None,
        "triaged_by":            None,
        "fulfillment_summary":   {"bto_id": None, "po_ids": []},
    }
    await db.stock_requests.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ── List ────────────────────────────────────────────────────────────────────
@router.get("")
async def list_requests(
    branch_id: Optional[str] = None,
    role: Optional[str] = None,     # 'requesting' | 'supplying' | 'all'
    status: Optional[str] = None,
    limit: int = 100,
    user=Depends(get_current_user),
):
    """List stock requests visible to caller.

    `role`:
       requesting → only requests THIS branch raised
       supplying  → only requests THIS branch is supplying
       all/None   → both (default)
    """
    check_perm(user, "branch_transfers", "view")
    query = {}
    if branch_id:
        if role == "requesting":
            query["requesting_branch_id"] = branch_id
        elif role == "supplying":
            query["supplying_branch_id"] = branch_id
        else:
            query["$or"] = [
                {"requesting_branch_id": branch_id},
                {"supplying_branch_id": branch_id},
            ]
    if status:
        query["status"] = status

    rows = await db.stock_requests.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)
    return {"items": rows, "total": len(rows)}


# ── Detail ──────────────────────────────────────────────────────────────────
@router.get("/products-lookup")
async def products_lookup(
    requesting_branch_id: str,
    supplying_branch_id: str,
    q: str = "",
    limit: int = 25,
    user=Depends(get_current_user),
):
    """Product search returning name + inventory in BOTH branches.

    Branch A (requesting) uses this when typing into the request form so
    they can see what Branch B actually has on hand before asking.
    Reuses the products collection but augments with branch-specific
    inventory counts.
    """
    check_perm(user, "products", "view")

    q = (q or "").strip()
    # Reuse the same shape as SmartProductSearch — start-of-word match
    # on name + sku. Case insensitive.
    match = {"active": {"$ne": False}}
    if q:
        match["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"sku":  {"$regex": q, "$options": "i"}},
        ]
    products = await db.products.find(
        match, {"_id": 0, "id": 1, "name": 1, "sku": 1, "unit": 1}
    ).limit(limit).to_list(limit)
    if not products:
        return {"items": []}

    pids = [p["id"] for p in products]
    # Pull inventory rows for BOTH branches in one query.
    inv_rows = await db.inventory.find(
        {"product_id": {"$in": pids},
         "branch_id":  {"$in": [requesting_branch_id, supplying_branch_id]}},
        {"_id": 0, "product_id": 1, "branch_id": 1, "quantity": 1},
    ).to_list(5000)
    by_pid = {}
    for r in inv_rows:
        by_pid.setdefault(r["product_id"], {})[r["branch_id"]] = float(r.get("quantity") or 0)

    items = []
    for p in products:
        inv = by_pid.get(p["id"], {})
        items.append({
            "id":             p["id"],
            "name":           p["name"],
            "sku":            p.get("sku", ""),
            "unit":           p.get("unit", ""),
            "requesting_qty": inv.get(requesting_branch_id, 0),
            "supplying_qty":  inv.get(supplying_branch_id, 0),
        })
    return {"items": items}


@router.get("/{request_id}")
async def get_request(request_id: str, user=Depends(get_current_user)):
    check_perm(user, "branch_transfers", "view")
    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")

    # Hydrate child docs (BTO + POs) so the FE can render status at a glance.
    bto_id = (req.get("fulfillment_summary") or {}).get("bto_id")
    po_ids = (req.get("fulfillment_summary") or {}).get("po_ids", [])

    bto = None
    if bto_id:
        bto = await db.branch_transfer_orders.find_one(
            {"id": bto_id},
            {"_id": 0, "id": 1, "order_number": 1, "status": 1, "items": 1,
             "total_at_transfer_capital": 1},
        )
    pos = []
    if po_ids:
        pos = await db.purchase_orders.find(
            {"id": {"$in": po_ids}},
            {"_id": 0, "id": 1, "po_number": 1, "vendor": 1, "status": 1,
             "grand_total": 1, "phantom_for_branch_id": 1,
             "items": 1, "ordered_at": 1, "received_at": 1,
             "received_date": 1,
             "ordered_by_name": 1, "supplier_ref": 1,
             "expected_delivery_date": 1, "ordered_notes": 1,
             "received_variance": 1, "received_variance_kind": 1},
        ).to_list(100)

    return {**req, "bto": bto, "pos": pos}


# ── Send (draft → sent) ─────────────────────────────────────────────────────
@router.post("/{request_id}/send")
async def send_request(request_id: str, user=Depends(get_current_user)):
    """Submit the request to the supplying branch."""
    check_perm(user, "branch_transfers", "create")
    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")
    if req["status"] != "draft":
        raise HTTPException(400, f"Cannot send request in status '{req['status']}'.")
    assert_branch_access(user, req["requesting_branch_id"])

    await db.stock_requests.update_one(
        {"id": request_id},
        {"$set": {
            "status":  "sent",
            "sent_at": now_iso(),
            "sent_by": user.get("full_name") or user.get("username", ""),
        }},
    )
    return {"ok": True, "status": "sent"}


# ── Triage (the heart of the workflow) ──────────────────────────────────────
@router.post("/{request_id}/triage")
async def triage_request(
    request_id: str, data: dict, user=Depends(get_current_user),
):
    """Branch B assigns each request line to a fulfillment type.

    Body:
      {
        assignments: [
          {item_id, fulfillment_type: 'transfer'|'supplier_po'|'unfulfilled',
           supplier_id?, supplier_name?, unit_price?, unit?, freight?, notes?}
        ],
        pin: "1234"      # branch fulfillment PIN — same as confirm-request
      }

    Atomic effects:
      * 'transfer' lines → coalesced into ONE BTO (supplying → requesting)
      * 'supplier_po' lines → grouped by supplier_id → ONE DRAFT PO per group
                              on the requesting branch's books, plus a
                              phantom mirror on the supplying branch
                              (same row — `phantom_for_branch_id` tags it)
      * 'unfulfilled' lines → variance log only
    """
    check_perm(user, "branch_transfers", "update")
    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")
    if req["status"] not in ("sent", "in_triage"):
        raise HTTPException(400, f"Cannot triage request in status '{req['status']}'.")

    sup_bid = req["supplying_branch_id"]
    req_bid = req["requesting_branch_id"]
    assert_branch_access(user, sup_bid)

    # PIN gate — same policy as confirm-request.
    pin = str(data.get("pin", "")).strip()
    if not pin:
        raise HTTPException(400, "PIN required to triage this request.")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(
        pin, "confirm_stock_request", branch_id=sup_bid,
    )
    if not verifier:
        raise HTTPException(403, "Invalid PIN or unauthorized for the supplying branch.")

    assignments = data.get("assignments", [])
    if not assignments:
        raise HTTPException(400, "No assignments provided.")
    by_item = {a["item_id"]: a for a in assignments}

    items = req.get("items", [])
    transfer_lines = []     # → BTO items
    po_lines_by_supplier = {}    # supplier_id → list of items
    unfulfilled_ids = set()

    for it in items:
        a = by_item.get(it["id"])
        if not a:
            # Unassigned lines auto-marked unfulfilled (safe default).
            unfulfilled_ids.add(it["id"])
            continue
        ftype = a.get("fulfillment_type")
        if ftype == "transfer":
            transfer_lines.append({**it, "_assign": a})
        elif ftype == "supplier_po":
            sid = (a.get("supplier_id") or "").strip()
            sname = (a.get("supplier_name") or "").strip()
            if not sid and not sname:
                raise HTTPException(400, f"Line {it['product_name']}: supplier required for supplier_po.")
            key = sid or f"name::{sname}"
            po_lines_by_supplier.setdefault(key, {"supplier_id": sid, "supplier_name": sname, "items": []})
            po_lines_by_supplier[key]["items"].append({**it, "_assign": a})
        elif ftype == "unfulfilled":
            unfulfilled_ids.add(it["id"])
        else:
            raise HTTPException(400, f"Unknown fulfillment_type '{ftype}' for {it['product_name']}.")

    org_id = req.get("organization_id") or get_org_context() or ""

    # ── 1. Spawn ONE BTO if any transfer lines ───────────────────────────
    bto_id = None
    bto_number = None
    if transfer_lines:
        from routes.branch_transfers import create_transfer
        bto_items = []
        for tl in transfer_lines:
            assign = tl["_assign"]
            bto_items.append({
                "product_id":   tl["product_id"],
                "product_name": tl["product_name"],
                "qty":          tl["qty"],
                "unit":         tl.get("unit", ""),
                # Capital prices will be filled in by the BTO send flow.
                "branch_capital": float(assign.get("branch_capital", 0)),
                "transfer_capital": float(assign.get("transfer_capital", 0)),
                "branch_retail": float(assign.get("branch_retail", 0)),
                # Carry the source request line id so we can update status later.
                "source_request_item_id": tl["id"],
            })
        bto_payload = {
            "from_branch_id":  sup_bid,
            "to_branch_id":    req_bid,
            "items":           bto_items,
            "notes":           f"Auto-generated from stock request {req['request_number']}",
            "source_request_id":     request_id,
            "source_request_number": req["request_number"],
        }
        bto_doc = await create_transfer(bto_payload, user=user)
        bto_id = bto_doc.get("id")
        bto_number = bto_doc.get("order_number")

        # `create_transfer` uses a hardcoded $set whitelist, so extra
        # linkage fields (`source_request_id`) need a follow-up write.
        if bto_id:
            await db.branch_transfer_orders.update_one(
                {"id": bto_id},
                {"$set": {
                    "source_request_id":     request_id,
                    "source_request_number": req["request_number"],
                }},
            )

    # ── 2. Spawn DRAFT POs grouped by supplier ───────────────────────────
    po_ids = []
    po_details = []
    for key, grp in po_lines_by_supplier.items():
        sid = grp["supplier_id"]
        sname = grp["supplier_name"]

        # Quick-create supplier on the requesting branch's directory if
        # the user only supplied a name. Idempotent via name match.
        if not sid and sname:
            existing = await db.suppliers.find_one(
                {"name": sname, "branch_id": req_bid}, {"_id": 0, "id": 1}
            )
            if existing:
                sid = existing["id"]
            else:
                sid = new_id()
                await db.suppliers.insert_one({
                    "id":              sid,
                    "name":            sname,
                    "branch_id":       req_bid,
                    "organization_id": org_id,
                    "created_at":      now_iso(),
                    "created_via":     "stock_request_quick_create",
                    "active":          True,
                })

        # Build PO items
        po_items = []
        line_subtotal = 0.0
        for line in grp["items"]:
            assign = line["_assign"]
            qty = float(line["qty"])
            up = float(assign.get("unit_price") or 0)
            total = round(qty * up, 2)
            po_items.append({
                "product_id":      line["product_id"],
                "product_name":    line["product_name"],
                "quantity":        qty,
                "unit_price":      up,
                "discount_type":   "amount",
                "discount_value":  0,
                "discount_amount": 0,
                "total":           total,
                "unit":            line.get("unit", ""),
                "source_request_item_id": line["id"],
            })
            line_subtotal += total

        po_number = await generate_next_number("PO", req_bid)
        po_doc = {
            "id":                       new_id(),
            "po_number":                po_number,
            "vendor":                   sname or "Supplier",
            "supplier_id":              sid,
            "branch_id":                req_bid,
            "organization_id":          org_id,
            "items":                    po_items,
            "line_subtotal":            round(line_subtotal, 2),
            "subtotal":                 round(line_subtotal, 2),
            "overall_discount_type":    "amount",
            "overall_discount_value":   0,
            "overall_discount_amount":  0,
            "freight":                  0,
            "tax_rate":                 0,
            "tax_amount":               0,
            "grand_total":              round(line_subtotal, 2),
            "status":                   "draft",   # ← NEW — Stock-Request triage draft
            "po_type":                  "draft",
            "payment_status":           "unpaid",
            "amount_paid":              0,
            "balance":                  round(line_subtotal, 2),
            # Stock-Request linkage
            "source_request_id":        request_id,
            "source_request_number":    req["request_number"],
            "phantom_for_branch_id":    sup_bid,   # supplying branch tracks via this
            "created_by":               user["id"],
            "created_by_name":          user.get("full_name") or user.get("username", ""),
            "created_at":               now_iso(),
        }
        await db.purchase_orders.insert_one(po_doc)
        po_doc.pop("_id", None)
        po_ids.append(po_doc["id"])
        po_details.append({
            "id":          po_doc["id"],
            "po_number":   po_number,
            "vendor":      sname or "Supplier",
            "supplier_id": sid,
            "grand_total": po_doc["grand_total"],
        })

    # ── 3. Update request items with their assignments ───────────────────
    updated_items = []
    for it in items:
        a = by_item.get(it["id"])
        if not a or it["id"] in unfulfilled_ids:
            updated_items.append({**it,
                "fulfillment_type": "unfulfilled",
                "status":           "unfulfilled"})
            continue
        ftype = a.get("fulfillment_type")
        if ftype == "transfer":
            updated_items.append({**it,
                "fulfillment_type":  "transfer",
                "assigned_doc_id":   bto_id,
                "assigned_doc_type": "bto",
                "status":            "assigned"})
        elif ftype == "supplier_po":
            sid = a.get("supplier_id") or ""
            sname = a.get("supplier_name") or ""
            # Find which PO this line ended up in
            matching = next((p for p in po_details
                             if (sid and p["supplier_id"] == sid)
                             or (not sid and p["vendor"] == sname)), None)
            updated_items.append({**it,
                "fulfillment_type":  "supplier_po",
                "supplier_id":       matching["supplier_id"] if matching else sid,
                "supplier_name":     sname or (matching["vendor"] if matching else ""),
                "assigned_doc_id":   matching["id"] if matching else None,
                "assigned_doc_type": "po",
                "status":            "assigned"})

    await db.stock_requests.update_one(
        {"id": request_id},
        {"$set": {
            "status":       "fulfillment_generated",
            "items":        updated_items,
            "triaged_at":   now_iso(),
            "triaged_by":   verifier.get("verifier_name") or user.get("full_name", ""),
            "fulfillment_summary": {
                "bto_id":     bto_id,
                "bto_number": bto_number,
                "po_ids":     po_ids,
                "po_details": po_details,
            },
        }},
    )

    return {
        "ok":           True,
        "bto_id":       bto_id,
        "bto_number":   bto_number,
        "po_ids":       po_ids,
        "po_details":   po_details,
        "unfulfilled":  len(unfulfilled_ids),
    }


# ── Cancel ──────────────────────────────────────────────────────────────────
@router.post("/{request_id}/cancel")
async def cancel_request(request_id: str, data: dict = None,
                         user=Depends(get_current_user)):
    """Cancel a request. Cascades to DRAFT-status child POs only —
    POs already moved to 'ordered' or beyond require manual cancel
    via the normal PO flow (intentional: once Branch B negotiated with
    a supplier, we don't auto-unwind that commitment)."""
    check_perm(user, "branch_transfers", "update")
    data = data or {}
    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")
    if req["status"] == "cancelled":
        return {"ok": True, "status": "cancelled", "already": True}

    # Cascade cancel of DRAFT child POs only.
    po_ids = (req.get("fulfillment_summary") or {}).get("po_ids", [])
    if po_ids:
        await db.purchase_orders.update_many(
            {"id": {"$in": po_ids}, "status": "draft"},
            {"$set": {"status": "cancelled",
                      "cancelled_at": now_iso(),
                      "cancelled_by": user.get("full_name", user.get("username", "")),
                      "cancel_reason": (data.get("reason") or "Stock request cancelled")}},
        )

    await db.stock_requests.update_one(
        {"id": request_id},
        {"$set": {"status": "cancelled",
                  "cancelled_at": now_iso(),
                  "cancelled_by": user.get("full_name", user.get("username", "")),
                  "cancel_reason": (data.get("reason") or "").strip()}},
    )
    return {"ok": True, "status": "cancelled"}



# ── Timeline (Phase 3+) ─────────────────────────────────────────────────────
@router.get("/{request_id}/timeline")
async def get_request_timeline(request_id: str, user=Depends(get_current_user)):
    """Aggregate every stamped event for the request + child docs into a
    single chronological feed. No new data is recorded — purely a
    presentation-layer roll-up over events already on stock_requests,
    branch_transfer_orders, and purchase_orders.
    """
    check_perm(user, "branch_transfers", "view")
    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")

    events = []
    req_ref = {"type": "request", "id": request_id,
               "number": req.get("request_number", "")}

    if req.get("created_at"):
        events.append({"at": req["created_at"], "kind": "request.created",
                       "label": "Stock request created",
                       "actor": req.get("created_by_name", ""),
                       "detail": f"{len(req.get('items', []))} line(s)",
                       "doc_ref": req_ref})
    if req.get("sent_at"):
        events.append({"at": req["sent_at"], "kind": "request.sent",
                       "label": "Sent to supplying branch",
                       "actor": req.get("sent_by") or req.get("created_by_name", ""),
                       "detail": "", "doc_ref": req_ref})
    if req.get("triaged_at"):
        summary = req.get("fulfillment_summary") or {}
        bits = []
        if summary.get("bto_number"):
            bits.append(f"BTO {summary['bto_number']}")
        if summary.get("po_details"):
            bits.append(f"{len(summary['po_details'])} PO(s)")
        events.append({"at": req["triaged_at"], "kind": "request.triaged",
                       "label": "Triaged into fulfillment plan",
                       "actor": req.get("triaged_by", ""),
                       "detail": " + ".join(bits) if bits else "",
                       "doc_ref": req_ref})
    if req.get("cancelled_at"):
        events.append({"at": req["cancelled_at"], "kind": "request.cancelled",
                       "label": "Request cancelled",
                       "actor": req.get("cancelled_by", ""),
                       "detail": req.get("cancel_reason", ""),
                       "doc_ref": req_ref})
    if req.get("completed_at"):
        events.append({"at": req["completed_at"], "kind": "request.completed",
                       "label": "Request completed", "actor": "",
                       "detail": "All child docs reached a terminal state.",
                       "doc_ref": req_ref})

    bto_id = (req.get("fulfillment_summary") or {}).get("bto_id")
    if bto_id:
        bto = await db.branch_transfer_orders.find_one(
            {"id": bto_id}, {"_id": 0}
        )
        if bto:
            bto_ref = {"type": "bto", "id": bto_id,
                       "number": bto.get("order_number", "")}
            if bto.get("created_at"):
                events.append({"at": bto["created_at"], "kind": "bto.created",
                               "label": "Branch Transfer created",
                               "actor": bto.get("created_by_name", ""),
                               "detail": f"{len(bto.get('items', []))} line(s)",
                               "doc_ref": bto_ref})
            if bto.get("sent_at"):
                events.append({"at": bto["sent_at"], "kind": "bto.sent",
                               "label": "BTO shipped",
                               "actor": bto.get("sent_by_name") or bto.get("sent_by") or "",
                               "detail": "", "doc_ref": bto_ref})
            if bto.get("received_at"):
                events.append({"at": bto["received_at"], "kind": "bto.received",
                               "label": "BTO received at destination",
                               "actor": bto.get("received_by_name") or bto.get("received_by") or "",
                               "detail": "", "doc_ref": bto_ref})
            if bto.get("cancelled_at"):
                events.append({"at": bto["cancelled_at"], "kind": "bto.cancelled",
                               "label": "BTO cancelled",
                               "actor": bto.get("cancelled_by", ""),
                               "detail": "", "doc_ref": bto_ref})

    po_ids = (req.get("fulfillment_summary") or {}).get("po_ids") or []
    if po_ids:
        pos = await db.purchase_orders.find(
            {"id": {"$in": po_ids}}, {"_id": 0}
        ).to_list(100)
        for po in pos:
            po_ref = {"type": "po", "id": po["id"],
                      "number": po.get("po_number", ""),
                      "vendor": po.get("vendor", "")}
            if po.get("created_at"):
                events.append({"at": po["created_at"], "kind": "po.created",
                               "label": f"DRAFT PO created · {po.get('vendor','')}",
                               "actor": po.get("created_by_name", ""),
                               "detail": f"{len(po.get('items', []))} line(s)",
                               "doc_ref": po_ref})
            if po.get("ordered_at"):
                bits = []
                if po.get("supplier_ref"):
                    bits.append(f"ref {po['supplier_ref']}")
                if po.get("expected_delivery_date"):
                    bits.append(f"ETA {po['expected_delivery_date']}")
                events.append({"at": po["ordered_at"], "kind": "po.ordered",
                               "label": f"PO marked Ordered · {po.get('vendor','')}",
                               "actor": po.get("ordered_by_name", ""),
                               "detail": " · ".join(bits),
                               "doc_ref": po_ref})
            if po.get("received_date"):
                vk = po.get("received_variance_kind") or ""
                bits = []
                if vk and vk != "completed":
                    bits.append(f"variance: {vk.replace('_',' ')}")
                events.append({"at": po["received_date"], "kind": "po.received",
                               "label": f"PO received · {po.get('vendor','')}",
                               "actor": "",
                               "detail": " · ".join(bits),
                               "doc_ref": po_ref})
            if po.get("cancelled_at"):
                events.append({"at": po["cancelled_at"], "kind": "po.cancelled",
                               "label": f"PO cancelled · {po.get('vendor','')}",
                               "actor": po.get("cancelled_by", ""),
                               "detail": po.get("cancel_reason", ""),
                               "doc_ref": po_ref})

    events.sort(key=lambda e: (e.get("at") or "9999"))
    return {"events": events, "count": len(events)}




# ── Mark Phantom PO Ordered (Phase 2) ───────────────────────────────────────
async def _notify_requesting_branch_po_ordered(po: dict, req: dict):
    """Best-effort SMS to the requesting branch's admins/manager when
    Branch B confirms with the supplier that the phantom PO has been
    ordered. Branch A then knows to expect delivery.

    Uses the existing SMS pipeline — silent on missing templates or
    recipients (consistent with branch_transfers pattern).
    """
    try:
        from routes.sms import queue_sms

        org_id = po.get("organization_id") or req.get("organization_id") or ""
        req_bid = req.get("requesting_branch_id") or po.get("branch_id") or ""
        if not org_id or not req_bid:
            return

        proj = {"_id": 0, "id": 1, "full_name": 1, "username": 1, "phone": 1,
                "phone_number": 1, "role": 1, "branch_id": 1, "branch_ids": 1}
        recipients = []
        seen = set()

        admins = await db.users.find(
            {"organization_id": org_id, "role": "admin", "active": True}, proj
        ).to_list(50)
        for u in admins:
            recipients.append(u)
            seen.add(u["id"])

        managers = await db.users.find(
            {"organization_id": org_id, "role": "manager", "active": True,
             "$or": [{"branch_id": req_bid}, {"branch_ids": req_bid}]},
            proj,
        ).to_list(50)
        for u in managers:
            if u["id"] in seen:
                continue
            recipients.append(u)
            seen.add(u["id"])

        branch = await _raw_db.branches.find_one(
            {"id": req_bid}, {"_id": 0, "name": 1}
        ) or {}
        org = await _raw_db.organizations.find_one(
            {"id": org_id}, {"_id": 0, "name": 1}
        ) or {}

        for rcp in recipients:
            phone = (rcp.get("phone") or rcp.get("phone_number") or "").strip()
            if not phone:
                continue
            await queue_sms(
                template_key="phantom_po_ordered",
                customer_id=rcp["id"],
                customer_name=rcp.get("full_name") or rcp.get("username", "Manager"),
                phone=phone,
                variables={
                    "recipient_name": rcp.get("full_name") or rcp.get("username", ""),
                    "po_number":      po.get("po_number", ""),
                    "vendor":         po.get("vendor", ""),
                    "branch_name":    branch.get("name", "your branch"),
                    "request_number": req.get("request_number", ""),
                    "grand_total":    f"{float(po.get('grand_total') or 0):,.2f}",
                    "company_name":   org.get("name", "AgriBooks"),
                    "items_count":    str(len(po.get("items", []))),
                    "delivery_date":  po.get("expected_delivery_date") or "",
                },
                organization_id=org_id,
                branch_id=req_bid,
                branch_name=branch.get("name", ""),
                trigger="auto",
                trigger_ref=po.get("id", ""),
                dedup_key=f"phantom_po_ordered:{po.get('id','')}:{rcp['id']}",
            )
    except Exception as e:
        import logging
        logging.getLogger("stock_requests").warning(
            f"phantom_po_ordered SMS failed: {e}"
        )


@router.post("/{request_id}/po/{po_id}/mark-ordered")
async def mark_phantom_po_ordered(
    request_id: str, po_id: str, data: dict = None,
    user=Depends(get_current_user),
):
    """Branch B confirms with the supplier and locks the PO.

    Body:
      {
        pin:                "1234",
        supplier_ref:       "INV-2026-…",
        expected_delivery_date: "2026-02-20",
        notes:              "",
        item_overrides: [
          {item_id, unit_price?, quantity?, discount_amount?}
        ],
        overall_discount?: 0,
        freight?:          0,
      }

    Atomic effects:
      * PO status: draft → ordered
      * Recomputes totals if any overrides supplied
      * Stamps ordered_at / ordered_by_name / supplier_ref
      * Fires SMS to requesting branch admins + manager(s)
      * Updates the linked stock-request item statuses → 'ordered'
    """
    check_perm(user, "branch_transfers", "update")
    data = data or {}

    req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Stock request not found.")
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(404, "PO not found.")
    if po.get("source_request_id") != request_id:
        raise HTTPException(400, "PO does not belong to this stock request.")
    if po.get("status") != "draft":
        raise HTTPException(400, f"Only DRAFT POs can be marked ordered "
                                 f"(current status: '{po.get('status')}').")
    sup_bid = req["supplying_branch_id"]
    assert_branch_access(user, sup_bid)

    pin = str(data.get("pin", "")).strip()
    if not pin:
        raise HTTPException(400, "PIN required to mark a phantom PO ordered.")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(
        pin, "confirm_stock_request", branch_id=sup_bid,
    )
    if not verifier:
        raise HTTPException(403, "Invalid PIN or unauthorized for the supplying branch.")

    overrides = {o["item_id"]: o for o in (data.get("item_overrides") or [])}
    new_items = []
    line_subtotal = 0.0
    for it in po.get("items", []):
        item_id = it.get("source_request_item_id") or it.get("id") or it.get("product_id")
        ov = overrides.get(item_id) or overrides.get(it.get("product_id"))
        next_item = dict(it)
        if ov:
            if ov.get("unit_price") is not None:
                next_item["unit_price"] = float(ov["unit_price"])
            if ov.get("quantity") is not None:
                next_item["quantity"] = float(ov["quantity"])
            if ov.get("discount_amount") is not None:
                next_item["discount_amount"] = float(ov["discount_amount"])
        qty = float(next_item.get("quantity") or 0)
        up = float(next_item.get("unit_price") or 0)
        disc = float(next_item.get("discount_amount") or 0)
        next_item["total"] = round(qty * up - disc, 2)
        line_subtotal += next_item["total"]
        new_items.append(next_item)

    overall_disc = float(data.get("overall_discount") or 0)
    freight = float(data.get("freight") or 0)
    grand_total = round(line_subtotal - overall_disc + freight, 2)

    # Snapshot of items + totals as ordered — the basis for post-receive
    # variance detection (Phase 3). Deep-copied to break aliasing.
    ordered_snapshot = {
        "items": [{
            "product_id":      it.get("product_id"),
            "product_name":    it.get("product_name", ""),
            "quantity":        float(it.get("quantity") or 0),
            "unit_price":      float(it.get("unit_price") or 0),
            "total":           float(it.get("total") or 0),
            "source_request_item_id": it.get("source_request_item_id"),
        } for it in new_items],
        "line_subtotal":           round(line_subtotal, 2),
        "overall_discount_amount": overall_disc,
        "freight":                 freight,
        "grand_total":             grand_total,
    }

    update_doc = {
        "items":                    new_items,
        "line_subtotal":            round(line_subtotal, 2),
        "subtotal":                 round(line_subtotal, 2),
        "overall_discount_amount":  overall_disc,
        "overall_discount_value":   overall_disc,
        "freight":                  freight,
        "grand_total":              grand_total,
        "balance":                  grand_total,
        "status":                   "ordered",
        "po_type":                  "terms",
        "ordered_at":               now_iso(),
        "ordered_by":               user["id"],
        "ordered_by_name":          (verifier.get("verifier_name")
                                     or user.get("full_name")
                                     or user.get("username", "")),
        "supplier_ref":             (data.get("supplier_ref") or "").strip(),
        "expected_delivery_date":   (data.get("expected_delivery_date") or "").strip(),
        "ordered_notes":            (data.get("notes") or "").strip(),
        "ordered_snapshot":         ordered_snapshot,
    }
    await db.purchase_orders.update_one({"id": po_id}, {"$set": update_doc})

    req_items = req.get("items", [])
    updated = []
    for it in req_items:
        if it.get("assigned_doc_id") == po_id:
            updated.append({**it, "status": "ordered"})
        else:
            updated.append(it)
    await db.stock_requests.update_one(
        {"id": request_id},
        {"$set": {"items": updated}},
    )

    fresh_po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await _notify_requesting_branch_po_ordered(fresh_po, req)

    return {
        "ok":             True,
        "po_id":          po_id,
        "po_number":      fresh_po.get("po_number"),
        "status":         "ordered",
        "grand_total":    grand_total,
        "ordered_by":     update_doc["ordered_by_name"],
        "ordered_at":     update_doc["ordered_at"],
    }


# ── Phase 3 — Post-receive variance detection ───────────────────────────────
def compute_phantom_po_variance(po: dict) -> dict:
    """Compare a phantom PO's `ordered_snapshot` against its current `items`
    (representing what was actually received). Returns a structured variance
    summary used both for back-propagation to the stock request and for UI.

    Variance kinds (mutually-exclusive — first match wins):
        completed        — every ordered line received in exact qty
        under_delivered  — at least one ordered line received with qty < ordered
        over_delivered   — at least one ordered line received with qty > ordered
        extra_items      — a line appears in received that wasn't in ordered
        missing_items    — an ordered line is entirely absent from received

    NOTE: a single PO can have multiple anomalies simultaneously. We pick a
    *primary* kind for the badge but expose `items_variance` for full detail.
    Precedence: missing_items > extra_items > under_delivered > over_delivered > completed.
    This ordering surfaces the most concerning anomaly to the supplying branch
    (missing items mean a supplier failed to ship; extra items hint at sloppy
    delivery; over/under are smaller deviations).
    """
    snapshot = po.get("ordered_snapshot") or {}
    ordered_items = snapshot.get("items") or []
    received_items = po.get("items") or []

    # Index by product_id (a line is identified by what product it shipped).
    ordered_by_pid = {it.get("product_id"): it for it in ordered_items if it.get("product_id")}
    received_by_pid = {it.get("product_id"): it for it in received_items if it.get("product_id")}

    items_variance = []
    has_missing = False
    has_extra = False
    has_under = False
    has_over = False

    all_pids = set(ordered_by_pid) | set(received_by_pid)
    for pid in all_pids:
        o = ordered_by_pid.get(pid)
        r = received_by_pid.get(pid)
        o_qty = float((o or {}).get("quantity") or 0)
        r_qty = float((r or {}).get("quantity") or 0)
        name = (r or o or {}).get("product_name", "")

        if o and not r:
            # Ordered but not received — supplier failed entirely on this line.
            items_variance.append({
                "product_id": pid, "product_name": name,
                "ordered_qty": o_qty, "received_qty": 0,
                "delta": -o_qty, "kind": "missing"})
            has_missing = True
        elif r and not o:
            # Received something that wasn't ordered.
            items_variance.append({
                "product_id": pid, "product_name": name,
                "ordered_qty": 0, "received_qty": r_qty,
                "delta": r_qty, "kind": "extra"})
            has_extra = True
        else:
            delta = round(r_qty - o_qty, 4)
            if delta == 0:
                items_variance.append({
                    "product_id": pid, "product_name": name,
                    "ordered_qty": o_qty, "received_qty": r_qty,
                    "delta": 0, "kind": "match"})
            elif delta < 0:
                items_variance.append({
                    "product_id": pid, "product_name": name,
                    "ordered_qty": o_qty, "received_qty": r_qty,
                    "delta": delta, "kind": "under"})
                has_under = True
            else:
                items_variance.append({
                    "product_id": pid, "product_name": name,
                    "ordered_qty": o_qty, "received_qty": r_qty,
                    "delta": delta, "kind": "over"})
                has_over = True

    # Primary classification, precedence missing > extra > under > over > complete.
    if has_missing:
        kind = "missing_items"
    elif has_extra:
        kind = "extra_items"
    elif has_under:
        kind = "under_delivered"
    elif has_over:
        kind = "over_delivered"
    else:
        kind = "completed"

    return {
        "kind":           kind,
        "items_variance": items_variance,
        "has_missing":    has_missing,
        "has_extra":      has_extra,
        "has_under":      has_under,
        "has_over":       has_over,
    }


async def _notify_supplying_branch_variance(po: dict, req: dict, variance: dict):
    """Best-effort SMS to the supplying branch's admins/manager when a
    phantom PO comes back from the supplier with a non-trivial variance
    (anything other than 'completed'). Lets Branch B chase the supplier
    without having to manually scan the request list.
    """
    if variance.get("kind") == "completed":
        return
    try:
        from routes.sms import queue_sms

        org_id = po.get("organization_id") or req.get("organization_id") or ""
        sup_bid = req.get("supplying_branch_id") or ""
        if not org_id or not sup_bid:
            return

        proj = {"_id": 0, "id": 1, "full_name": 1, "username": 1,
                "phone": 1, "phone_number": 1, "role": 1,
                "branch_id": 1, "branch_ids": 1}
        recipients = []
        seen = set()

        admins = await db.users.find(
            {"organization_id": org_id, "role": "admin", "active": True}, proj
        ).to_list(50)
        for u in admins:
            recipients.append(u)
            seen.add(u["id"])
        managers = await db.users.find(
            {"organization_id": org_id, "role": "manager", "active": True,
             "$or": [{"branch_id": sup_bid}, {"branch_ids": sup_bid}]}, proj
        ).to_list(50)
        for u in managers:
            if u["id"] in seen:
                continue
            recipients.append(u)
            seen.add(u["id"])

        branch = await _raw_db.branches.find_one(
            {"id": sup_bid}, {"_id": 0, "name": 1}) or {}
        org = await _raw_db.organizations.find_one(
            {"id": org_id}, {"_id": 0, "name": 1}) or {}

        # Build a compact human summary of the worst-offending lines.
        bad = [iv for iv in variance.get("items_variance", [])
               if iv["kind"] in ("missing", "under", "extra", "over")]
        bits = []
        for iv in bad[:3]:  # cap at 3 lines for SMS length sanity
            if iv["kind"] == "missing":
                bits.append(f"{iv['product_name']} missing (ordered {iv['ordered_qty']})")
            elif iv["kind"] == "extra":
                bits.append(f"{iv['product_name']} extra (+{iv['received_qty']})")
            elif iv["kind"] == "under":
                bits.append(f"{iv['product_name']} short ({iv['received_qty']}/{iv['ordered_qty']})")
            elif iv["kind"] == "over":
                bits.append(f"{iv['product_name']} over (+{iv['delta']})")
        if len(bad) > 3:
            bits.append(f"+{len(bad)-3} more")
        summary = "; ".join(bits) if bits else "see PO"

        labels = {
            "under_delivered": "under-delivery",
            "over_delivered":  "over-delivery",
            "extra_items":     "extra items",
            "missing_items":   "missing items",
        }
        kind_label = labels.get(variance["kind"], variance["kind"])

        for rcp in recipients:
            phone = (rcp.get("phone") or rcp.get("phone_number") or "").strip()
            if not phone:
                continue
            await queue_sms(
                template_key="phantom_po_variance",
                customer_id=rcp["id"],
                customer_name=rcp.get("full_name") or rcp.get("username", "Manager"),
                phone=phone,
                variables={
                    "recipient_name":      rcp.get("full_name") or rcp.get("username", ""),
                    "po_number":           po.get("po_number", ""),
                    "vendor":              po.get("vendor", ""),
                    "request_number":      req.get("request_number", ""),
                    "variance_kind":       variance["kind"],
                    "variance_kind_label": kind_label,
                    "variance_summary":    summary,
                    "branch_name":         branch.get("name", ""),
                    "company_name":        org.get("name", "AgriBooks"),
                },
                organization_id=org_id,
                branch_id=sup_bid,
                branch_name=branch.get("name", ""),
                trigger="auto",
                trigger_ref=po.get("id", ""),
                dedup_key=f"phantom_po_variance:{po.get('id','')}:{rcp['id']}",
            )
    except Exception as e:
        import logging
        logging.getLogger("stock_requests").warning(
            f"phantom_po_variance SMS failed: {e}"
        )


async def update_phantom_po_received(po: dict):
    """Hook called from `purchase_orders.receive_purchase_order` whenever a
    PO that was spawned from a stock request transitions to `received`.

    Computes variance, stamps `received_variance` on the PO, and back-
    propagates per-item statuses to the stock_request so the supplying
    branch sees supplier-reliability anomalies in its phantom-PO list.

    Best-effort — never raises (logging on failure).
    """
    try:
        request_id = po.get("source_request_id")
        if not request_id:
            return
        variance = compute_phantom_po_variance(po)

        await db.purchase_orders.update_one(
            {"id": po["id"]},
            {"$set": {"received_variance": variance,
                      "received_variance_kind": variance["kind"]}},
        )

        req = await db.stock_requests.find_one({"id": request_id}, {"_id": 0})
        if not req:
            return
        po_id = po["id"]
        new_status = {
            "completed":        "completed",
            "under_delivered":  "under_delivered",
            "over_delivered":   "over_delivered",
            "extra_items":      "extra_items",
            "missing_items":    "missing_items",
        }.get(variance["kind"], "received")

        updated = []
        for it in req.get("items", []):
            if it.get("assigned_doc_id") == po_id:
                updated.append({**it,
                                "status":   new_status,
                                "variance": variance["kind"]})
            else:
                updated.append(it)

        # Fire variance SMS to supplying branch (best-effort, no-op on completed).
        await _notify_supplying_branch_variance(po, req, variance)

        # If every child doc is now in a terminal state, mark the request completed.
        all_pos = await db.purchase_orders.find(
            {"source_request_id": request_id, "id": {"$ne": None}},
            {"_id": 0, "id": 1, "status": 1},
        ).to_list(100)
        bto = None
        if (req.get("fulfillment_summary") or {}).get("bto_id"):
            bto = await db.branch_transfer_orders.find_one(
                {"id": req["fulfillment_summary"]["bto_id"]},
                {"_id": 0, "status": 1},
            )

        all_pos_done = all(p["status"] in ("received", "cancelled") for p in all_pos) if all_pos else True
        bto_done = (bto is None) or (bto.get("status") in ("completed", "received", "cancelled"))
        new_request_status = req["status"]
        update_extra = {}
        if all_pos_done and bto_done and req["status"] == "fulfillment_generated":
            new_request_status = "completed"
            update_extra["completed_at"] = now_iso()

        await db.stock_requests.update_one(
            {"id": request_id},
            {"$set": {"items": updated, "status": new_request_status, **update_extra}},
        )
    except Exception as e:
        import logging
        logging.getLogger("stock_requests").warning(
            f"update_phantom_po_received failed for PO {po.get('id')}: {e}"
        )

