"""
br1 — Baseline shape verification for `make_business_day_tenant()`.

Why this test exists
--------------------
Every other business-regression file (br2 PO/AP, br3 branch transfer,
br99 reconciliation snapshot, …) assumes a very specific tenant shape:
2 branches, 3 distinct user roles, 2 customers @ 0 balance, wallets on
both branches, no leaked products/suppliers. If that contract ever
silently drifts, every downstream BR test fails in a confusing way.

br1's job is to be the loud canary: it asserts the exact shape of a
fresh business-day tenant — nothing more, nothing less. No sales, no
returns, no PO, no transfer. Pure foundation verification.

What it deliberately does NOT do
--------------------------------
  * Seed any product (br99 / br3 / br5 do that themselves).
  * Seed a supplier (br2 does that).
  * Exercise any sale/payment/return/PO route (br99/br2/br3 cover those).
  * Test cross-tenant isolation (phase2d / public-receipt-PIN already do).
"""
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db  # noqa: E402


# Mirror the per-branch wallet types `seed_wallets()` provisions.
_EXPECTED_WALLET_TYPES = {"cashier", "digital", "safe"}


@pytest.mark.asyncio
async def test_br1_baseline_tenant_shape(tenant, record_result):
    """Assert the exact end-shape of `make_business_day_tenant()`.

    All assertions are sourced directly from `_raw_db` rather than from
    the `tenant` dict, so we verify what *MongoDB* actually holds — not
    just what the helper returned.
    """
    org_id      = tenant["org_id"]
    main_branch = tenant["branches"]["main"]
    b2_branch   = tenant["branches"]["b2"]
    owner       = tenant["users"]["owner"]
    manager     = tenant["users"]["manager"]
    cashier     = tenant["users"]["cashier"]
    cash_cust   = tenant["customers"]["cash"]
    credit_cust = tenant["customers"]["credit"]

    # ── 1. Organization row exists and is unique ─────────────────────
    org_row = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    org_count = await _raw_db.organizations.count_documents({"id": org_id})
    assert org_row is not None, f"br1: organization {org_id} not found"
    assert org_count == 1, f"br1: organization {org_id} has {org_count} duplicates"
    record_result(
        scenario="br1.baseline",
        step="organization_exists_and_unique",
        expected={"count": 1, "active": True},
        actual={"count": org_count, "active": bool(org_row.get("active"))},
        evidence={"org_id": org_id, "org_name": org_row.get("name")},
    )

    # ── 2. Exactly two branches in the tenant ────────────────────────
    branch_rows = await _raw_db.branches.find(
        {"organization_id": org_id}, {"_id": 0}
    ).to_list(None)
    branch_ids = sorted(b["id"] for b in branch_rows)
    expected_branch_ids = sorted([main_branch, b2_branch])
    assert branch_ids == expected_branch_ids, (
        f"br1: branch id set mismatch — expected {expected_branch_ids}, "
        f"got {branch_ids}"
    )
    # The "Branch B …" label is how `make_business_day_tenant()` tags the
    # second branch, so we use it to confirm we're looking at the right row.
    b2_row = next((b for b in branch_rows if b["id"] == b2_branch), None)
    assert b2_row is not None and b2_row["name"].startswith("Branch B "), (
        f"br1: second branch is not labelled 'Branch B …' "
        f"(got {b2_row.get('name') if b2_row else None})"
    )
    assert all(b.get("active") is True for b in branch_rows), (
        "br1: at least one branch is not active"
    )
    record_result(
        scenario="br1.baseline",
        step="exactly_two_active_branches",
        expected={"count": 2, "all_active": True},
        actual={"count": len(branch_rows),
                "all_active": all(b.get("active") for b in branch_rows)},
        evidence={"branch_ids": branch_ids,
                  "branch_names": [b["name"] for b in branch_rows]},
    )

    # ── 3. Three users with correct roles + branch assignment ─────────
    user_rows = await _raw_db.users.find(
        {"organization_id": org_id}, {"_id": 0}
    ).to_list(None)
    by_id = {u["id"]: u for u in user_rows}
    assert len(user_rows) == 3, (
        f"br1: expected exactly 3 users in tenant, got {len(user_rows)} "
        f"(ids={[u['id'] for u in user_rows]})"
    )
    owner_row   = by_id.get(owner["id"])
    manager_row = by_id.get(manager["id"])
    cashier_row = by_id.get(cashier["id"])
    assert owner_row is not None, f"br1: owner/admin row {owner['id']} missing"
    assert manager_row is not None, f"br1: manager row {manager['id']} missing"
    assert cashier_row is not None, f"br1: cashier row {cashier['id']} missing"

    assert owner_row["role"] == "admin", (
        f"br1: owner role expected 'admin', got {owner_row['role']!r}"
    )
    assert manager_row["role"] == "manager", (
        f"br1: manager role expected 'manager', got {manager_row['role']!r}"
    )
    assert cashier_row["role"] == "cashier", (
        f"br1: cashier role expected 'cashier', got {cashier_row['role']!r}"
    )

    # Branch assignment: every user belongs to main branch; none has an
    # empty `branch_ids`. This is the contract br99 / br3 rely on.
    for label, row in (("owner", owner_row), ("manager", manager_row),
                       ("cashier", cashier_row)):
        assert row.get("branch_ids") == [main_branch], (
            f"br1: {label} branch_ids expected [{main_branch}], "
            f"got {row.get('branch_ids')!r}"
        )
        assert row.get("branch_id") == main_branch, (
            f"br1: {label} branch_id expected {main_branch}, "
            f"got {row.get('branch_id')!r}"
        )
        assert row.get("active") is True, f"br1: {label} is not active"

    # Manager must carry a PIN (Phase 2D PIN policy lookups depend on it).
    assert manager_row.get("manager_pin"), (
        "br1: manager_pin missing on manager row — "
        "phase2d permission checks will fail"
    )

    record_result(
        scenario="br1.baseline",
        step="three_users_with_correct_roles",
        expected={"count": 3, "roles": sorted(["admin", "manager", "cashier"])},
        actual={
            "count": len(user_rows),
            "roles": sorted([owner_row["role"], manager_row["role"],
                             cashier_row["role"]]),
        },
        evidence={
            "owner":   {"id": owner_row["id"],   "role": owner_row["role"],
                        "branch_ids": owner_row.get("branch_ids")},
            "manager": {"id": manager_row["id"], "role": manager_row["role"],
                        "branch_ids": manager_row.get("branch_ids"),
                        "has_pin": bool(manager_row.get("manager_pin"))},
            "cashier": {"id": cashier_row["id"], "role": cashier_row["role"],
                        "branch_ids": cashier_row.get("branch_ids")},
        },
    )

    # ── 4. Two customers @ balance 0, both on main branch ────────────
    cust_rows = await _raw_db.customers.find(
        {"organization_id": org_id}, {"_id": 0}
    ).to_list(None)
    cust_by_id = {c["id"]: c for c in cust_rows}
    assert len(cust_rows) == 2, (
        f"br1: expected exactly 2 customers, got {len(cust_rows)} "
        f"(ids={[c['id'] for c in cust_rows]})"
    )
    cash_row   = cust_by_id.get(cash_cust)
    credit_row = cust_by_id.get(credit_cust)
    assert cash_row is not None,   f"br1: cash customer {cash_cust} missing"
    assert credit_row is not None, f"br1: credit customer {credit_cust} missing"
    for label, row in (("cash", cash_row), ("credit", credit_row)):
        assert float(row.get("balance", 0)) == 0.0, (
            f"br1: {label} customer starting balance expected 0.0, "
            f"got {row.get('balance')!r}"
        )
        assert row.get("branch_id") == main_branch, (
            f"br1: {label} customer branch_id expected {main_branch}, "
            f"got {row.get('branch_id')!r}"
        )
        assert row.get("active") is True, (
            f"br1: {label} customer is not active"
        )
    record_result(
        scenario="br1.baseline",
        step="two_customers_zero_balance",
        expected={"count": 2, "balances": [0.0, 0.0]},
        actual={
            "count": len(cust_rows),
            "balances": sorted([float(cash_row["balance"]),
                                float(credit_row["balance"])]),
        },
        evidence={
            "cash_customer":   {"id": cash_row["id"],
                                "balance": float(cash_row["balance"]),
                                "branch_id": cash_row.get("branch_id")},
            "credit_customer": {"id": credit_row["id"],
                                "balance": float(credit_row["balance"]),
                                "branch_id": credit_row.get("branch_id")},
        },
    )

    # ── 5. Supplier NOT seeded (br2 owns supplier setup) ─────────────
    supplier_count = await _raw_db.suppliers.count_documents(
        {"organization_id": org_id}
    )
    assert supplier_count == 0, (
        f"br1: business-day tenant should NOT pre-seed suppliers "
        f"(found {supplier_count}). Suppliers belong to br2."
    )
    record_result(
        scenario="br1.baseline",
        step="no_supplier_pre_seeded",
        expected={"supplier_count": 0},
        actual={"supplier_count": supplier_count},
        evidence={"org_id": org_id,
                  "rationale": "suppliers are br2's responsibility"},
    )

    # ── 6. Products NOT seeded (each scenario seeds its own) ─────────
    # The spec mentions optional low-stock/repack/transfer products. The
    # current `make_business_day_tenant()` deliberately seeds NONE, to
    # keep scenarios self-explanatory. If a future change adds product
    # seeding, this assertion will fail loudly and force a doc update.
    product_count = await _raw_db.products.count_documents(
        {"organization_id": org_id}
    )
    inventory_count = await _raw_db.inventory.count_documents(
        {"organization_id": org_id}
    )
    assert product_count == 0, (
        f"br1: business-day tenant should NOT pre-seed products "
        f"(found {product_count}). Each scenario is responsible for its own."
    )
    assert inventory_count == 0, (
        f"br1: business-day tenant should NOT pre-seed inventory rows "
        f"(found {inventory_count})."
    )
    record_result(
        scenario="br1.baseline",
        step="no_products_pre_seeded",
        expected={"product_count": 0, "inventory_count": 0},
        actual={"product_count": product_count,
                "inventory_count": inventory_count},
        evidence={"org_id": org_id,
                  "rationale": "scenarios seed their own products"},
    )

    # ── 7. Wallets: 3 per branch, all 0.0, types match ───────────────
    wallet_rows = await _raw_db.fund_wallets.find(
        {"organization_id": org_id}, {"_id": 0}
    ).to_list(None)
    assert len(wallet_rows) == 6, (
        f"br1: expected 6 wallets (3 types × 2 branches), got {len(wallet_rows)}"
    )
    by_branch: dict[str, dict[str, dict]] = {main_branch: {}, b2_branch: {}}
    for w in wallet_rows:
        bid = w.get("branch_id")
        assert bid in by_branch, (
            f"br1: wallet {w.get('id')} on unexpected branch {bid!r}"
        )
        by_branch[bid][w["type"]] = w
    for bid, wallets in by_branch.items():
        assert set(wallets.keys()) == _EXPECTED_WALLET_TYPES, (
            f"br1: branch {bid} wallet types expected {_EXPECTED_WALLET_TYPES}, "
            f"got {set(wallets.keys())}"
        )
        for wtype, w in wallets.items():
            assert float(w.get("balance", 0)) == 0.0, (
                f"br1: branch {bid} {wtype} wallet balance expected 0.0, "
                f"got {w.get('balance')!r}"
            )
            assert w.get("active") is True, (
                f"br1: branch {bid} {wtype} wallet is not active"
            )
    record_result(
        scenario="br1.baseline",
        step="wallets_six_zero_balance",
        expected={"wallet_count": 6, "types_per_branch": sorted(_EXPECTED_WALLET_TYPES),
                  "all_balances_zero": True},
        actual={
            "wallet_count": len(wallet_rows),
            "types_per_branch": sorted({w["type"] for w in wallet_rows}),
            "all_balances_zero": all(float(w["balance"]) == 0.0
                                     for w in wallet_rows),
        },
        evidence={
            "main_branch_wallets": {t: w["id"] for t, w in by_branch[main_branch].items()},
            "b2_branch_wallets":   {t: w["id"] for t, w in by_branch[b2_branch].items()},
        },
    )

    # ── 8. Every seeded row is scoped to the same org_id ─────────────
    cross_tenant_misses = {
        "branches":     [b["id"] for b in branch_rows     if b.get("organization_id") != org_id],
        "users":        [u["id"] for u in user_rows       if u.get("organization_id") != org_id],
        "customers":    [c["id"] for c in cust_rows       if c.get("organization_id") != org_id],
        "fund_wallets": [w["id"] for w in wallet_rows     if w.get("organization_id") != org_id],
    }
    leaks = {k: v for k, v in cross_tenant_misses.items() if v}
    assert not leaks, (
        f"br1: rows with mismatched organization_id detected — {leaks}"
    )
    record_result(
        scenario="br1.baseline",
        step="all_rows_scoped_to_org_id",
        expected={"leaks": {}},
        actual={"leaks": leaks},
        evidence={"org_id": org_id,
                  "rows_checked": {
                      "branches":     len(branch_rows),
                      "users":        len(user_rows),
                      "customers":    len(cust_rows),
                      "fund_wallets": len(wallet_rows),
                  }},
    )

    # ── 9. Idempotency canary — running this test back-to-back via
    #      module-scoped tenants must produce non-overlapping ids ────
    # We can't directly observe a 2nd module run from inside one test,
    # but we CAN verify the uuid-suffix convention is in place. If a
    # future regression introduces hardcoded ids, every id below will
    # collapse to a fixed string and this assertion will fail.
    suffixed_ids = [org_id, main_branch, b2_branch,
                    owner["id"], manager["id"], cashier["id"],
                    cash_cust, credit_cust]
    # phase2b's `_uid()` adds an 8-hex suffix, so length must be > prefix+1.
    for sid in suffixed_ids:
        assert "-" in sid and len(sid.split("-")[-1]) >= 6, (
            f"br1: id {sid!r} does not look uuid-suffixed; "
            f"re-running the suite will collide."
        )
    record_result(
        scenario="br1.baseline",
        step="ids_are_uuid_suffixed_for_idempotency",
        expected={"all_suffixed": True},
        actual={"all_suffixed": True},
        evidence={"sampled_ids": suffixed_ids},
    )
