"""
br_prep — Prepared-order completion visibility regression.

Locks in the H-1 (visibility side-effect) fix: when a "for_preparation"
draft is finalized through `POST /api/unified-sale` with
`draft_invoice_id`, the persisted invoice MUST remain visible through
every tenant-scoped read.

Why this file exists
--------------------
The legacy finalize path used `db.invoices.replace_one(filter, invoice)`.
`replace_one` is NOT wrapped by `TenantCollection` (only `insert_one`,
`update_one`, `find_one_and_update` are), so:
  * the filter was not org-scoped, and
  * the replacement document was written verbatim with NO
    `organization_id` field (the proxy's `_inject_org` was bypassed).
The result was an orphan invoice: customer.balance correctly bumped via
the proxied `customers.update_one` $inc, but the invoice row invisible
to `/api/invoices`, `/api/customers/receivables-summary`, and every
other tenant-scoped query. The live sample site has ₱170,331 of AR
across 2 customers in exactly this state.

The fix (commit 200b6c1e, 2026-05-09): replaced `replace_one` with a
guarded `find_one_and_update` (atomic status flip) plus an
`update_one({"id": ...}, {"$set": invoice})`. Both go through the
proxy. The `$set` payload has no `organization_id` field, so the
org_id stamped at draft-create time is preserved.

The regression assertion that catches the bug
----------------------------------------------
After finalize, we MUST be able to fetch the invoice via the proxied
`db.invoices.find_one({"id": ...})`. If `organization_id` is missing
or wrong on the persisted row, the proxy returns None and the test
fails. This is the single assertion that the H-1 closure was
originally missing.

Scope: 5 scenarios — one per payment type the user reported impact for:
  a) cash   → status=paid, no AR, cashier wallet bumped
  b) digital→ status=paid, digital metadata preserved, digital wallet bumped
  c) split  → status=paid, both wallets bumped, no AR
  d) partial→ status=partial, partial paid, balance to AR
  e) credit → status=open, full balance to AR
"""
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import db, _raw_db, set_org_context  # noqa: E402
from tests.business_regression._fixtures import seed_product  # noqa: E402
from tests.phase2b._fixtures import seed_customer  # noqa: E402
from routes.draft_orders import create_draft  # noqa: E402
from routes.sales import create_unified_sale  # noqa: E402


# ─── Local helpers (kept file-local; promote later if a 2nd BR needs them) ──
def _draft_payload(branch_id, customer_id, customer_name, product_id, *,
                   qty=1, rate=100):
    """Shape accepted by routes.draft_orders.create_draft."""
    line_total = qty * rate
    return {
        "branch_id": branch_id,
        "items": [{
            "product_id": product_id, "product_name": "BR Prep Product",
            "sku": f"SKU-{product_id[-6:]}", "quantity": qty,
            "rate": rate, "unit_price": rate, "price": rate,
            "total": line_total, "discount_amount": 0, "is_repack": False,
        }],
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_phone": "", "customer_address": "",
        "subtotal": line_total, "freight": 0,
        "overall_discount": 0, "grand_total": line_total,
        "sale_mode": "quick",
        "active_scheme": "retail",
        "notes": "br_prep test",
    }


def _finalize_payload(branch_id, customer_id, customer_name, product_id,
                      *, qty, rate, payment_type, amount_paid,
                      payment_method, fund_source,
                      digital_meta=None, draft_invoice_id=None):
    """Shape accepted by routes.sales.create_unified_sale."""
    line_total = qty * rate
    balance = round(line_total - amount_paid, 2)
    base = {
        "branch_id": branch_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "items": [{
            "product_id": product_id, "product_name": "BR Prep Product",
            "quantity": qty, "rate": rate,
            "discount_type": "amount", "discount_value": 0,
        }],
        "subtotal": line_total, "freight": 0, "overall_discount": 0,
        "grand_total": line_total,
        "amount_paid": amount_paid,
        "balance": balance,
        "payment_type": payment_type,
        "payment_method": payment_method,
        "fund_source": fund_source,
        "sale_type": "walk_in",
        "mode": "quick",
        "release_mode": "full",
    }
    if draft_invoice_id:
        base["draft_invoice_id"] = draft_invoice_id
    if digital_meta:
        base.update(digital_meta)
    return base


async def _wallet_balance(branch_id, wtype):
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": wtype, "active": True},
        {"_id": 0, "balance": 1},
    )
    return float((w or {}).get("balance", 0.0))


async def _movements_for(inv_id):
    return await _raw_db.movements.count_documents(
        {"reference_id": inv_id, "type": "sale"}
    )


async def _customer_balance(customer_id):
    c = await _raw_db.customers.find_one(
        {"id": customer_id}, {"_id": 0, "balance": 1}
    )
    return float((c or {}).get("balance", 0.0))


async def _payments_visible(customer_id):
    """Simulate the /customers/receivables-summary visibility test —
    count tenant-scoped open/partial invoices for this customer."""
    cur = db.invoices.find(
        {"customer_id": customer_id,
         "status": {"$nin": ["voided", "paid"]},
         "balance": {"$gt": 0}},
        {"_id": 0, "id": 1, "balance": 1},
    )
    rows = await cur.to_list(50)
    return len(rows), round(sum(float(r.get("balance") or 0) for r in rows), 2)


async def _create_and_finalize(
    tenant, *, payment_type, amount_paid, payment_method, fund_source,
    digital_meta=None, qty=2, rate=100,
):
    """Common arrange-act: build draft → finalize → return both ids
    plus the finalize response. Stock seeded high enough so guard never trips."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    cash_cust = tenant["customers"]["cash"]

    set_org_context(org_id)

    # AR-bearing payment types must use a customer with credit_limit > 0.
    # Walk-in/no-customer is invalid for partial/credit (route rejects it).
    # We seed a FRESH per-scenario customer for credit/partial so cross-
    # scenario state in the module-scoped tenant doesn't contaminate the
    # "open invoice count for this customer" assertion.
    if payment_type in ("credit", "partial"):
        customer_id = await seed_customer(
            org_id, main, name=f"BR Prep Cust {payment_type}",
            balance=0.0, credit_limit=100_000.0,
        )
        customer_name = f"BR Prep Cust {payment_type}"
    else:
        customer_id = cash_cust
        cust_doc = await _raw_db.customers.find_one(
            {"id": cash_cust}, {"_id": 0, "name": 1}
        )
        customer_name = (cust_doc or {}).get("name", "BR Prep Cust")

    product_id = await seed_product(
        org_id, main, name=f"BR Prep {payment_type}",
        price=rate, stock=20, cost=50,
    )

    draft = await create_draft(
        _draft_payload(main, customer_id, customer_name, product_id,
                       qty=qty, rate=rate),
        user=owner_user,
    )
    draft_id = draft["id"]
    draft_inv_number = draft["invoice_number"]
    draft_doc_code = draft.get("doc_code")

    fin = await create_unified_sale(
        _finalize_payload(
            main, customer_id, customer_name, product_id,
            qty=qty, rate=rate, payment_type=payment_type,
            amount_paid=amount_paid, payment_method=payment_method,
            fund_source=fund_source, digital_meta=digital_meta,
            draft_invoice_id=draft_id,
        ),
        user=owner_user,
    )

    return {
        "org_id": org_id, "main": main,
        "customer_id": customer_id, "customer_name": customer_name,
        "product_id": product_id,
        "draft_id": draft_id, "draft_invoice_number": draft_inv_number,
        "draft_doc_code": draft_doc_code,
        "finalize_response": fin,
    }


async def _assert_post_finalize_visibility(
    record_result, *, scenario, ctx, expected_status,
    expected_balance, expected_paid_amount,
    expected_payments_visible_count,
):
    """Shared assertion block executed by every scenario."""
    org_id = ctx["org_id"]
    customer_id = ctx["customer_id"]
    draft_id = ctx["draft_id"]
    draft_inv_number = ctx["draft_invoice_number"]
    draft_doc_code = ctx["draft_doc_code"]
    fin = ctx["finalize_response"]

    # ── REGRESSION KEY: tenant-scoped find_one MUST see the invoice ───
    # This is the assertion that would have caught the old replace_one
    # org_id-strip bug. If org_id is missing on the persisted row, the
    # proxy will return None here.
    inv_via_proxy = await db.invoices.find_one(
        {"id": draft_id}, {"_id": 0},
    )
    expected = {"visible_via_proxy": True}
    actual = {"visible_via_proxy": inv_via_proxy is not None}
    record_result(scenario, "invoice_visible_via_tenant_proxy",
                  expected, actual, {
                      "org_id": org_id, "invoice_id": draft_id,
                      "rationale": ("This is the regression assertion. "
                                    "If org_id is stripped, the proxy "
                                    "returns None and this fails."),
                  })
    assert inv_via_proxy is not None, (
        "tenant-scoped invoice.find_one returned None — org_id was stripped"
    )

    # ── organization_id matches tenant ────────────────────────────────
    record_result(scenario, "invoice_organization_id_matches_tenant",
                  {"organization_id": org_id},
                  {"organization_id": inv_via_proxy.get("organization_id")},
                  {"invoice_id": draft_id})
    assert inv_via_proxy.get("organization_id") == org_id

    # ── invoice_number preserved from draft ───────────────────────────
    record_result(scenario, "invoice_number_preserved_from_draft",
                  {"invoice_number": draft_inv_number},
                  {"invoice_number": inv_via_proxy.get("invoice_number")},
                  {})
    assert inv_via_proxy.get("invoice_number") == draft_inv_number

    # ── doc_code preserved from draft (was reserved at draft create) ──
    record_result(scenario, "doc_code_preserved_from_draft",
                  {"doc_code_present": bool(draft_doc_code),
                   "matches_draft": True},
                  {"doc_code_present": bool(inv_via_proxy.get("doc_code")),
                   "matches_draft": inv_via_proxy.get("doc_code") == draft_doc_code},
                  {"doc_code": inv_via_proxy.get("doc_code")})
    assert inv_via_proxy.get("doc_code") == draft_doc_code

    # ── status flipped away from for_preparation, to expected final ────
    record_result(scenario, "status_finalized",
                  {"status": expected_status},
                  {"status": inv_via_proxy.get("status")},
                  {"rationale": "Original draft status was 'for_preparation'"})
    assert inv_via_proxy.get("status") == expected_status

    # ── balance & amount_paid match expectation ───────────────────────
    record_result(scenario, "balance_and_amount_paid",
                  {"balance": expected_balance,
                   "amount_paid": expected_paid_amount},
                  {"balance": round(float(inv_via_proxy.get("balance") or 0), 2),
                   "amount_paid": round(float(inv_via_proxy.get("amount_paid") or 0), 2)},
                  {})
    assert round(float(inv_via_proxy.get("balance") or 0), 2) == expected_balance
    assert round(float(inv_via_proxy.get("amount_paid") or 0), 2) == expected_paid_amount

    # ── customer_id stored correctly ──────────────────────────────────
    record_result(scenario, "customer_id_present",
                  {"customer_id": customer_id},
                  {"customer_id": inv_via_proxy.get("customer_id")}, {})
    assert inv_via_proxy.get("customer_id") == customer_id

    # ── no duplicate row for this draft id ────────────────────────────
    dup_count = await _raw_db.invoices.count_documents({"id": draft_id})
    record_result(scenario, "no_duplicate_invoice_for_draft_id",
                  {"row_count": 1}, {"row_count": dup_count},
                  {"draft_id": draft_id})
    assert dup_count == 1

    # ── no extra invoice with the same invoice_number ─────────────────
    same_num = await _raw_db.invoices.count_documents(
        {"invoice_number": draft_inv_number, "organization_id": org_id}
    )
    record_result(scenario, "no_duplicate_invoice_number_in_tenant",
                  {"row_count": 1}, {"row_count": same_num}, {})
    assert same_num == 1

    # ── stock deducted exactly once for this invoice ──────────────────
    mv = await _movements_for(draft_id)
    record_result(scenario, "stock_movement_recorded_exactly_once",
                  {"movement_count": 1}, {"movement_count": mv}, {})
    assert mv == 1

    # ── Sales History visibility: /api/invoices respects tenant ───────
    listing = await db.invoices.find(
        {"status": {"$ne": "voided"}}, {"_id": 0, "id": 1, "status": 1},
    ).to_list(2000)
    in_sales_history = any(r.get("id") == draft_id for r in listing)
    record_result(scenario, "visible_in_sales_history_listing",
                  {"in_listing": True}, {"in_listing": in_sales_history},
                  {"sales_history_size": len(listing)})
    assert in_sales_history

    # ── Payments / receivables visibility ─────────────────────────────
    open_count, open_sum = await _payments_visible(customer_id)
    record_result(scenario, "payments_receivables_visibility",
                  {"open_invoice_count_for_customer": expected_payments_visible_count,
                   "open_balance_includes_this_invoice": expected_payments_visible_count > 0},
                  {"open_invoice_count_for_customer": open_count,
                   "open_balance_includes_this_invoice": open_sum >= expected_balance and expected_balance > 0},
                  {"open_sum": open_sum})
    assert open_count == expected_payments_visible_count

    # ── completion response carries id/invoice_number for the UI ──────
    record_result(scenario, "completion_response_has_receipt_fields",
                  {"has_id": True, "has_invoice_number": True},
                  {"has_id": bool(fin.get("id")),
                   "has_invoice_number": bool(fin.get("invoice_number"))},
                  {"response_id": fin.get("id"),
                   "response_invoice_number": fin.get("invoice_number")})
    assert fin.get("id") == draft_id
    assert fin.get("invoice_number") == draft_inv_number


# ─────────────────────────────────────────────────────────────────────
# SCENARIO A — cash
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_a_cash(tenant, record_result):
    SCEN = "br_prep.a_cash"
    cashier_before = await _wallet_balance(tenant["branches"]["main"], "cashier")
    cash_cust_balance_before = await _customer_balance(tenant["customers"]["cash"])

    ctx = await _create_and_finalize(
        tenant, payment_type="cash",
        amount_paid=200.0, payment_method="Cash",
        fund_source="cashier", qty=2, rate=100,
    )

    await _assert_post_finalize_visibility(
        record_result, scenario=SCEN, ctx=ctx,
        expected_status="paid",
        expected_balance=0.0, expected_paid_amount=200.0,
        expected_payments_visible_count=0,   # paid → no AR
    )

    cashier_after = await _wallet_balance(tenant["branches"]["main"], "cashier")
    record_result(SCEN, "cashier_wallet_credited_full_amount",
                  {"delta": 200.0}, {"delta": round(cashier_after - cashier_before, 2)}, {})
    assert round(cashier_after - cashier_before, 2) == 200.0

    cust_after = await _customer_balance(tenant["customers"]["cash"])
    record_result(SCEN, "no_AR_for_cash_sale",
                  {"customer_balance_delta": 0.0},
                  {"customer_balance_delta": round(cust_after - cash_cust_balance_before, 2)}, {})
    assert round(cust_after - cash_cust_balance_before, 2) == 0.0


# ─────────────────────────────────────────────────────────────────────
# SCENARIO B — digital
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_b_digital(tenant, record_result):
    SCEN = "br_prep.b_digital"
    digital_before = await _wallet_balance(tenant["branches"]["main"], "digital")
    cash_cust_balance_before = await _customer_balance(tenant["customers"]["cash"])

    ctx = await _create_and_finalize(
        tenant, payment_type="digital",
        amount_paid=300.0, payment_method="GCash",
        fund_source="digital",
        digital_meta={"digital_platform": "GCash",
                      "digital_ref_number": "REF-BR-PREP-B",
                      "digital_sender": "Test Sender"},
        qty=3, rate=100,
    )

    await _assert_post_finalize_visibility(
        record_result, scenario=SCEN, ctx=ctx,
        expected_status="paid",
        expected_balance=0.0, expected_paid_amount=300.0,
        expected_payments_visible_count=0,
    )

    # Digital metadata persisted on the invoice
    inv = await db.invoices.find_one({"id": ctx["draft_id"]}, {"_id": 0})
    record_result(SCEN, "digital_metadata_preserved",
                  {"digital_platform": "GCash",
                   "digital_ref_number": "REF-BR-PREP-B",
                   "digital_sender": "Test Sender"},
                  {"digital_platform": inv.get("digital_platform"),
                   "digital_ref_number": inv.get("digital_ref_number"),
                   "digital_sender": inv.get("digital_sender")},
                  {})
    assert inv.get("digital_platform") == "GCash"
    assert inv.get("digital_ref_number") == "REF-BR-PREP-B"
    assert inv.get("digital_sender") == "Test Sender"

    digital_after = await _wallet_balance(tenant["branches"]["main"], "digital")
    record_result(SCEN, "digital_wallet_credited_full_amount",
                  {"delta": 300.0}, {"delta": round(digital_after - digital_before, 2)}, {})
    assert round(digital_after - digital_before, 2) == 300.0

    cust_after = await _customer_balance(tenant["customers"]["cash"])
    record_result(SCEN, "no_AR_for_digital_sale",
                  {"customer_balance_delta": 0.0},
                  {"customer_balance_delta": round(cust_after - cash_cust_balance_before, 2)}, {})
    assert round(cust_after - cash_cust_balance_before, 2) == 0.0


# ─────────────────────────────────────────────────────────────────────
# SCENARIO C — split
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_c_split(tenant, record_result):
    SCEN = "br_prep.c_split"
    cashier_before = await _wallet_balance(tenant["branches"]["main"], "cashier")
    digital_before = await _wallet_balance(tenant["branches"]["main"], "digital")

    ctx = await _create_and_finalize(
        tenant, payment_type="split",
        amount_paid=400.0, payment_method="Split",
        fund_source="split",
        digital_meta={"digital_platform": "GCash",
                      "digital_ref_number": "REF-BR-PREP-C",
                      "cash_amount": 150.0, "digital_amount": 250.0},
        qty=4, rate=100,
    )

    await _assert_post_finalize_visibility(
        record_result, scenario=SCEN, ctx=ctx,
        expected_status="paid",
        expected_balance=0.0, expected_paid_amount=400.0,
        expected_payments_visible_count=0,
    )

    inv = await db.invoices.find_one({"id": ctx["draft_id"]}, {"_id": 0})
    payments = inv.get("payments") or []
    cash_sum = round(sum(float(p.get("amount") or 0) for p in payments
                         if p.get("fund_source") == "cashier"), 2)
    digi_sum = round(sum(float(p.get("amount") or 0) for p in payments
                         if p.get("fund_source") == "digital"), 2)
    record_result(SCEN, "split_payment_breakdown_preserved",
                  {"cash_portion": 150.0, "digital_portion": 250.0},
                  {"cash_portion": cash_sum, "digital_portion": digi_sum},
                  {"payments_rows": len(payments)})
    assert cash_sum == 150.0
    assert digi_sum == 250.0

    cashier_after = await _wallet_balance(tenant["branches"]["main"], "cashier")
    digital_after = await _wallet_balance(tenant["branches"]["main"], "digital")
    record_result(SCEN, "both_wallets_credited",
                  {"cashier_delta": 150.0, "digital_delta": 250.0},
                  {"cashier_delta": round(cashier_after - cashier_before, 2),
                   "digital_delta": round(digital_after - digital_before, 2)}, {})
    assert round(cashier_after - cashier_before, 2) == 150.0
    assert round(digital_after - digital_before, 2) == 250.0


# ─────────────────────────────────────────────────────────────────────
# SCENARIO D — partial
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_d_partial(tenant, record_result):
    SCEN = "br_prep.d_partial"
    cashier_before = await _wallet_balance(tenant["branches"]["main"], "cashier")

    ctx = await _create_and_finalize(
        tenant, payment_type="partial",
        amount_paid=120.0, payment_method="Cash",
        fund_source="cashier",
        qty=5, rate=100,   # grand_total = 500, partial 120, balance 380
    )

    await _assert_post_finalize_visibility(
        record_result, scenario=SCEN, ctx=ctx,
        expected_status="partial",
        expected_balance=380.0, expected_paid_amount=120.0,
        expected_payments_visible_count=1,   # this invoice IS the receivable
    )

    cashier_after = await _wallet_balance(tenant["branches"]["main"], "cashier")
    record_result(SCEN, "cashier_wallet_credited_paid_portion_only",
                  {"delta": 120.0}, {"delta": round(cashier_after - cashier_before, 2)}, {})
    assert round(cashier_after - cashier_before, 2) == 120.0

    cust_after = await _customer_balance(ctx["customer_id"])
    record_result(SCEN, "customer_balance_increased_by_unpaid_portion_only",
                  {"customer_balance_delta": 380.0},
                  {"customer_balance_delta": round(cust_after, 2)}, {})
    assert round(cust_after, 2) == 380.0


# ─────────────────────────────────────────────────────────────────────
# SCENARIO E — credit (THE user-reported live symptom)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_e_credit(tenant, record_result):
    SCEN = "br_prep.e_credit"
    cashier_before = await _wallet_balance(tenant["branches"]["main"], "cashier")

    ctx = await _create_and_finalize(
        tenant, payment_type="credit",
        amount_paid=0.0, payment_method="Credit",
        fund_source="cashier",
        qty=6, rate=100,   # grand_total = 600, all to AR
    )

    await _assert_post_finalize_visibility(
        record_result, scenario=SCEN, ctx=ctx,
        expected_status="open",
        expected_balance=600.0, expected_paid_amount=0.0,
        expected_payments_visible_count=1,
    )

    cashier_after = await _wallet_balance(tenant["branches"]["main"], "cashier")
    record_result(SCEN, "no_wallet_movement_for_credit_sale",
                  {"delta": 0.0}, {"delta": round(cashier_after - cashier_before, 2)}, {})
    assert round(cashier_after - cashier_before, 2) == 0.0

    cust_after = await _customer_balance(ctx["customer_id"])
    record_result(SCEN, "customer_balance_increased_by_full_grand_total",
                  {"customer_balance_delta": 600.0},
                  {"customer_balance_delta": round(cust_after, 2)}, {})
    assert round(cust_after, 2) == 600.0


# ─────────────────────────────────────────────────────────────────────
# SCENARIO F — Backfill regression: orphan invoice + dry-run + apply
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_prep_f_orphan_invoice_backfill(tenant, record_result):
    """End-to-end test of the backfill endpoint:
      1. Inject a synthetic orphan invoice (organization_id missing) via
         `_raw_db.invoices.insert_one` so it bypasses the tenant proxy
         the same way the legacy `replace_one` did.
      2. Confirm tenant-scoped read does NOT see it.
      3. Run dry-run → reports the orphan, no writes.
      4. Run apply → stamps organization_id, writes audit log row.
      5. Re-run dry-run → reports zero orphans for this id.
      6. Tenant-scoped read NOW sees it.
      7. Confirm conflicts/unresolved rows are skipped, never stamped.
    """
    SCEN = "br_prep.f_orphan_backfill"
    from routes.admin_backfill_invoices_org_id import _sweep
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    credit_cust = tenant["customers"]["credit"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    from utils import new_id, now_iso
    # --- 1. Inject a synthetic orphan (no organization_id) -------------
    orphan_id = new_id()
    orphan_num = f"ORPHAN-{orphan_id[-6:]}"
    await _raw_db.invoices.insert_one({
        "id": orphan_id,
        "invoice_number": orphan_num,
        "branch_id": main,
        "customer_id": credit_cust,
        "cashier_id": owner_user["id"],
        "status": "open",
        "balance": 999.0,
        "grand_total": 999.0,
        "amount_paid": 0.0,
        "payment_type": "credit",
        "items": [],
        "created_at": now_iso(),
        # deliberately NO organization_id — this is the orphan signature.
    })

    # --- 2. Tenant-scoped read does NOT see it -------------------------
    pre = await db.invoices.find_one({"id": orphan_id}, {"_id": 0})
    record_result(SCEN, "orphan_invisible_via_tenant_proxy_before_backfill",
                  {"visible_via_proxy": False},
                  {"visible_via_proxy": pre is not None},
                  {"orphan_id": orphan_id, "invoice_number": orphan_num})
    assert pre is None

    # --- 3. Dry-run reports the orphan ---------------------------------
    dry = await _sweep(apply=False, requester=owner_user)
    found_in_dryrun = any(
        s.get("invoice_id") == orphan_id for s in dry["updated_samples"]
    )
    record_result(SCEN, "dryrun_reports_orphan_and_does_not_write",
                  {"finds_orphan": True, "mode": "dry-run"},
                  {"finds_orphan": found_in_dryrun, "mode": dry["mode"]},
                  {"scanned": dry["scanned"], "missing_org_id": dry["missing_org_id"]})
    assert dry["mode"] == "dry-run"
    assert found_in_dryrun

    # Dry-run must not have written organization_id
    still_orphan = await _raw_db.invoices.find_one(
        {"id": orphan_id}, {"_id": 0, "organization_id": 1}
    )
    assert not still_orphan.get("organization_id"), \
        "dry-run must not stamp organization_id"

    # --- 4. Apply stamps + writes audit log row ------------------------
    audit_before = await _raw_db.admin_backfill_log.count_documents(
        {"kind": "invoices_org_id_backfill", "invoice_id": orphan_id}
    )
    applied = await _sweep(apply=True, requester=owner_user)
    audit_after = await _raw_db.admin_backfill_log.count_documents(
        {"kind": "invoices_org_id_backfill", "invoice_id": orphan_id}
    )
    record_result(SCEN, "apply_stamps_org_id_and_audit_log",
                  {"mode": "apply", "audit_rows_added": 1},
                  {"mode": applied["mode"], "audit_rows_added": audit_after - audit_before},
                  {"updated_count": applied["resolved_by_branch"]
                                    + applied["resolved_by_customer"]
                                    + applied["resolved_by_cashier"]
                                    + applied["multi_path_agreed"]})
    assert applied["mode"] == "apply"
    assert audit_after - audit_before == 1

    # Confirm the raw row now carries org_id
    stamped = await _raw_db.invoices.find_one(
        {"id": orphan_id}, {"_id": 0, "organization_id": 1,
                            "_org_id_backfilled_source": 1}
    )
    record_result(SCEN, "raw_row_organization_id_stamped",
                  {"organization_id": org_id},
                  {"organization_id": stamped.get("organization_id")},
                  {"resolution_source": stamped.get("_org_id_backfilled_source")})
    assert stamped.get("organization_id") == org_id

    # --- 5. Re-run dry-run → no more orphan for this id ---------------
    dry2 = await _sweep(apply=False, requester=owner_user)
    found_again = any(
        s.get("invoice_id") == orphan_id for s in dry2["updated_samples"]
    )
    record_result(SCEN, "rerun_dryrun_does_not_re_resolve_stamped_row",
                  {"finds_orphan_again": False},
                  {"finds_orphan_again": found_again}, {})
    assert not found_again

    # --- 6. Tenant-scoped read NOW sees it ----------------------------
    post = await db.invoices.find_one({"id": orphan_id}, {"_id": 0})
    record_result(SCEN, "orphan_visible_via_tenant_proxy_after_backfill",
                  {"visible_via_proxy": True},
                  {"visible_via_proxy": post is not None},
                  {"orphan_id": orphan_id})
    assert post is not None

    # --- 7. Conflict & unresolved rows are skipped, never guessed -----
    # 7a. Unresolved: orphan with NO branch_id / customer_id / cashier_id
    unresolved_id = new_id()
    await _raw_db.invoices.insert_one({
        "id": unresolved_id,
        "invoice_number": f"UNRES-{unresolved_id[-6:]}",
        "status": "open", "balance": 0,
        "items": [], "created_at": now_iso(),
        "branch_id": "", "customer_id": "", "cashier_id": "",
    })
    # 7b. Conflict: branch in tenant A, customer in tenant B (real)
    # Build a stray "second org" by inserting a tiny branch with a
    # different org_id and a customer pointing to that other org.
    other_org = "br-prep-other-org-" + new_id()[-6:]
    stray_branch = new_id()
    stray_cust = new_id()
    await _raw_db.branches.insert_one({
        "id": stray_branch, "organization_id": other_org,
        "name": "Stray Branch", "active": True,
    })
    await _raw_db.customers.insert_one({
        "id": stray_cust, "organization_id": org_id,    # tenant A
        "branch_id": main, "name": "Stray Customer", "active": True,
    })
    conflict_id = new_id()
    await _raw_db.invoices.insert_one({
        "id": conflict_id,
        "invoice_number": f"CONF-{conflict_id[-6:]}",
        "status": "open", "balance": 0, "items": [],
        "branch_id": stray_branch,    # resolves to other_org
        "customer_id": stray_cust,    # resolves to org_id
        "cashier_id": "",
        "created_at": now_iso(),
    })

    applied2 = await _sweep(apply=True, requester=owner_user)
    # Neither should be stamped
    after_unresolved = await _raw_db.invoices.find_one(
        {"id": unresolved_id}, {"_id": 0, "organization_id": 1}
    )
    after_conflict = await _raw_db.invoices.find_one(
        {"id": conflict_id}, {"_id": 0, "organization_id": 1}
    )
    record_result(SCEN, "unresolved_and_conflict_rows_skipped",
                  {"unresolved_stamped": False, "conflict_stamped": False,
                   "unresolved_in_report": True, "conflict_in_report": True},
                  {"unresolved_stamped": bool(after_unresolved.get("organization_id")),
                   "conflict_stamped": bool(after_conflict.get("organization_id")),
                   "unresolved_in_report": applied2["unresolved"] >= 1,
                   "conflict_in_report": applied2["multi_path_conflict"] >= 1},
                  {"unresolved_count": applied2["unresolved"],
                   "conflict_count": applied2["multi_path_conflict"]})
    assert not after_unresolved.get("organization_id")
    assert not after_conflict.get("organization_id")
    assert applied2["unresolved"] >= 1
    assert applied2["multi_path_conflict"] >= 1

    # Cleanup the synthetic rows we injected via _raw_db so module-end
    # teardown stays at zero footprint. (The module teardown's
    # cleanup_business_tenant deletes by organization_id; rows we
    # stamped with this tenant's org are covered, but the unresolved
    # row has empty org_id and the conflict row's branch belongs to
    # `other_org` — delete them explicitly here.)
    await _raw_db.invoices.delete_many(
        {"id": {"$in": [unresolved_id, conflict_id]}}
    )
    await _raw_db.branches.delete_many({"id": stray_branch})
    # stray_cust carries our org_id, so module teardown handles it.
