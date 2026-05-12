"""
business_regression — extension fixtures over phase2b/_fixtures.

DESIGN
------
We deliberately re-use everything from `tests/phase2b/_fixtures.py`:
  * `_uid()`            — randomised id helper
  * `make_tenant()`     — single org + 1 branch + 1 admin
  * `seed_wallets()`    — cashier/digital/safe wallets per branch
  * `seed_product()`    — product + branch inventory row
  * `seed_customer()`   — customer with optional starting balance
  * `seed_supplier()`   — supplier row (used by future BR2)
  * `fake_user()`       — dict shape consumed by route handlers
  * `snapshot_inventory()` / `snapshot_customer()` / `snapshot_wallet()`
  * `base_sale_payload()`

The ONLY new helpers added here are the two missing pieces phase2b
does NOT cover:

  1. `make_business_day_tenant()` — same shape as `make_tenant()` but
     returns a *complete store-day* tenant: 2 branches, 3 distinct user
     roles (owner / manager / cashier), 2 wallets sets, 2 customers
     (cash + credit). It composes phase2b helpers rather than
     reimplementing them.

  2. `cleanup_business_tenant(org_id)` — surgical deletion across
     every TENANT_COLLECTION plus the small set of non-tenant audit
     collections (`pin_attempt_log`, `qr_action_log`, `view_tokens`,
     `journal_entries`, `notifications`). Used by conftest teardown.

Anything sales/return/payment-related continues to be exercised through
the actual route handlers (`create_unified_sale`, `record_invoice_payment`,
`create_return`) — we do NOT wrap or re-implement those.
"""
import os
import sys
from typing import Optional

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Re-use everything phase2b already provides. Do NOT duplicate.
from tests.phase2b._fixtures import (  # noqa: E402,F401
    _uid,
    make_tenant,
    seed_wallets,
    seed_product,
    seed_customer,
    seed_supplier,
    fake_user,
    snapshot_inventory,
    snapshot_customer,
    snapshot_wallet,
    snapshot_invoice,
    base_sale_payload,
)
from config import _raw_db, TENANT_COLLECTIONS  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# New helper #1 — business-day tenant builder.
#
# Why not extend phase2b.make_tenant? phase2b's helper is intentionally
# minimal (1 branch, 1 admin) because every g1–g7 test wants a clean
# small footprint. The business-regression suite needs the FULL store
# shape (2 branches, owner/manager/cashier) for cross-branch invariants
# and permission tests later. Composing on top of phase2b keeps a single
# source of truth for org/branch row shape.
# ─────────────────────────────────────────────────────────────────────
async def make_business_day_tenant():
    """Build a complete store-day tenant in one call.

    Returns a dict:
      {
        "org_id":            str,
        "branches":          {"main": str, "b2": str},
        "users":             {"owner": dict, "manager": dict, "cashier": dict},
        "customers":         {"cash": str, "credit": str},
        "wallets":           {"main": {...}, "b2": {...}},   # per-branch wallet ids
      }

    The returned `users` are full FastAPI-shape dicts ready to pass as
    `user=` to route handlers (mirrors `fake_user()` shape from phase2b).
    Stock / supplier / repack products are NOT seeded here — they belong
    to each scenario's test arrange step so the test stays self-explanatory.
    """
    # phase2b.make_tenant() gives us org + main branch + admin in one go.
    # Use that as the foundation and just bolt on the extras.
    org_id, main_branch_id, owner_id = await make_tenant()

    # Second branch in the same org.
    b2_branch_id = _uid("br-b2-br")
    await _raw_db.branches.insert_one({
        "id": b2_branch_id, "organization_id": org_id,
        "name": f"Branch B {b2_branch_id[-4:]}", "active": True,
    })

    # Wallets for BOTH branches.
    main_wallets = await seed_wallets(org_id, main_branch_id)
    b2_wallets = await seed_wallets(org_id, b2_branch_id)

    # Manager + cashier users alongside the admin/owner. We use full
    # `fake_user()` dicts so they're route-handler-ready, but we ALSO
    # insert real `users` rows so anything that calls
    # `db.users.find_one(...)` (e.g. PIN policy lookups in verify.py)
    # can resolve them in this tenant context.
    mgr_id = _uid("br-mgr")
    cashier_id = _uid("br-cashier")
    await _raw_db.users.insert_many([
        {
            "id": mgr_id, "username": f"mgr-{mgr_id[-4:]}",
            "full_name": "BR Manager", "organization_id": org_id,
            "role": "manager", "active": True,
            "branch_ids": [main_branch_id], "branch_id": main_branch_id,
            "manager_pin": _uid("pin")[-6:],
        },
        {
            "id": cashier_id, "username": f"cashier-{cashier_id[-4:]}",
            "full_name": "BR Cashier", "organization_id": org_id,
            "role": "cashier", "active": True,
            "branch_ids": [main_branch_id], "branch_id": main_branch_id,
        },
    ])

    # Two customers — neither carries a starting balance so we can do
    # crisp `expected vs actual` AR math.
    cash_cust_id = await seed_customer(org_id, main_branch_id,
                                       name=f"BR Cash Cust {_uid('c')[-4:]}",
                                       balance=0)
    credit_cust_id = await seed_customer(org_id, main_branch_id,
                                         name=f"BR Credit Cust {_uid('c')[-4:]}",
                                         balance=0)

    owner_user = fake_user(org_id, owner_id, branch_id=main_branch_id, role="admin")
    manager_user = fake_user(org_id, mgr_id, branch_id=main_branch_id, role="manager")
    cashier_user = fake_user(org_id, cashier_id, branch_id=main_branch_id, role="cashier")

    return {
        "org_id": org_id,
        "branches": {"main": main_branch_id, "b2": b2_branch_id},
        "users": {"owner": owner_user, "manager": manager_user, "cashier": cashier_user},
        "customers": {"cash": cash_cust_id, "credit": credit_cust_id},
        "wallets": {"main": main_wallets, "b2": b2_wallets},
    }


# ─────────────────────────────────────────────────────────────────────
# New helper #2 — cleanup by organization_id.
#
# phase2b never bothered with explicit cleanup (every test makes a
# fresh tenant with uuid-suffixed ids, so collisions are impossible
# and the rows just accumulate harmlessly). For the business-regression
# suite we DO want explicit cleanup so:
#   * repeated CI runs don't grow the shared test DB indefinitely,
#   * a test that fails halfway doesn't leak per-tenant data into the
#     deploy-gate baseline (`/api/admin/customer-balance-reconciliation`
#     would otherwise list these as unresolved drifts forever).
# ─────────────────────────────────────────────────────────────────────
# Non-tenant audit collections that store an `organization_id` field
# but are NOT in TENANT_COLLECTIONS (so they are accessed via _raw_db).
_AUDIT_COLLECTIONS_WITH_ORG_FIELD = [
    "pin_attempt_log",
    "qr_action_log",
    "doc_codes",
    "view_tokens",
    "journal_entries",
    "stock_movements",
    "movements",
    "security_events",
]


async def cleanup_business_tenant(org_id: Optional[str]):
    """Delete every row anywhere in the database that carries this
    `organization_id`. Safe to call multiple times.

    Implementation: walk the union of TENANT_COLLECTIONS plus our small
    audit-collection allow-list and run `delete_many({"organization_id": org_id})`
    on each via `_raw_db` (bypassing the fail-closed tenant proxy).
    """
    if not org_id:
        return
    seen = set()
    targets = list(TENANT_COLLECTIONS) + _AUDIT_COLLECTIONS_WITH_ORG_FIELD
    for name in targets:
        if name in seen:
            continue
        seen.add(name)
        try:
            await _raw_db[name].delete_many({"organization_id": org_id})
        except Exception:
            # Best-effort. A missing collection just means there was
            # nothing to delete anyway.
            pass
    # The org row itself lives in `organizations` (not tenant-scoped by
    # `organization_id`); its primary key is `id`.
    try:
        await _raw_db.organizations.delete_many({"id": org_id})
    except Exception:
        pass
