"""
Phase 2B — shared fixture builder for Core Business Flow Verification Matrix.

Goal: spin up a complete throw-away tenant in a few lines so each test can
focus on the invariant it verifies, not on plumbing. NEVER touches production
tenants — every org_id / branch_id / product_id is uuid-suffixed.

Provides:
  * make_tenant()       — org + branch + admin user
  * seed_product()      — product + inventory row in branch
  * seed_customer()     — customer w/ optional starting balance
  * seed_supplier()     — supplier w/ optional payable
  * seed_wallets()      — fund_wallets (cashier, digital, safe) for branch
  * fake_user()         — dict shape consumed by route handlers
  * snapshot_inventory() / snapshot_customer() / snapshot_invoice()
                        — read current state for before/after asserts
"""
import uuid
import sys
import os
from typing import Optional

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def make_tenant():
    """Create an isolated org + branch + admin for one test."""
    org_id = _uid("p2b-org")
    branch_id = _uid("p2b-br")
    admin_id = _uid("p2b-adm")
    set_org_context(org_id)

    await _raw_db.organizations.insert_one({
        "id": org_id, "name": f"Tenant {org_id[-4:]}", "active": True,
    })
    await _raw_db.branches.insert_one({
        "id": branch_id, "organization_id": org_id,
        "name": f"Branch {branch_id[-4:]}", "active": True,
    })
    await _raw_db.users.insert_one({
        "id": admin_id, "username": f"admin-{admin_id[-4:]}",
        "full_name": "Phase 2B Admin",
        "organization_id": org_id, "role": "admin",
        "active": True, "branch_ids": [branch_id], "branch_id": branch_id,
        "permissions": {},
    })
    return org_id, branch_id, admin_id


async def seed_wallets(org_id: str, branch_id: str):
    """Provision the three fund wallets the sales/payment routes expect."""
    cashier_id = _uid("w-cash")
    digital_id = _uid("w-digi")
    safe_id = _uid("w-safe")
    await _raw_db.fund_wallets.insert_many([
        {"id": cashier_id, "organization_id": org_id, "branch_id": branch_id,
         "type": "cashier", "balance": 0.0, "active": True},
        {"id": digital_id, "organization_id": org_id, "branch_id": branch_id,
         "type": "digital", "balance": 0.0, "active": True},
        {"id": safe_id, "organization_id": org_id, "branch_id": branch_id,
         "type": "safe", "balance": 0.0, "active": True},
    ])
    return {"cashier": cashier_id, "digital": digital_id, "safe": safe_id}


async def seed_product(
    org_id: str, branch_id: str,
    *, name: str = "Test Product", price: float = 100.0,
    stock: float = 50.0, cost: float = 60.0,
):
    """Create a product (org-level) + inventory row (branch-level)."""
    product_id = _uid("prd")
    await _raw_db.products.insert_one({
        "id": product_id, "organization_id": org_id,
        "name": name, "sku": f"SKU-{product_id[-6:]}",
        "category": "General", "unit": "pc",
        "price": price, "cost": cost,
        "active": True, "is_repack": False,
    })
    await _raw_db.inventory.insert_one({
        "id": _uid("inv"), "organization_id": org_id,
        "branch_id": branch_id, "product_id": product_id,
        "quantity": stock, "cost_price": cost,
    })
    return product_id


async def seed_customer(
    org_id: str, branch_id: str,
    *, name: Optional[str] = None, balance: float = 0.0,
    credit_limit: float = 100_000.0, interest_rate: float = 0.0,
):
    cid = _uid("cust")
    await _raw_db.customers.insert_one({
        "id": cid, "organization_id": org_id, "branch_id": branch_id,
        "name": name or f"Cust {cid[-4:]}", "active": True,
        "balance": balance, "credit_limit": credit_limit,
        "interest_rate": interest_rate, "grace_period": 0,
    })
    return cid


async def seed_supplier(org_id: str, *, name: Optional[str] = None, payable: float = 0.0):
    sid = _uid("supp")
    await _raw_db.suppliers.insert_one({
        "id": sid, "organization_id": org_id,
        "name": name or f"Supplier {sid[-4:]}",
        "active": True, "payable": payable,
    })
    return sid


def fake_user(org_id: str, admin_id: str, *, branch_id: str = "", role: str = "admin", perms: Optional[dict] = None):
    return {
        "id": admin_id, "username": "p2b-tester", "full_name": "P2B Tester",
        "organization_id": org_id, "role": role,
        "branch_id": branch_id, "branch_ids": [branch_id] if branch_id else [],
        "active": True,
        "permissions": perms or {
            "sales": {"create": True, "read": True, "update": True, "delete": True},
            "accounting": {"receive_payment": True, "create": True, "read": True},
            "pos": {"sell": True},
            "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
            "inventory": {"read": True, "update": True, "create": True},
            "returns": {"create": True, "read": True},
            "customers": {"read": True, "create": True, "update": True},
        },
    }


async def snapshot_inventory(branch_id: str, product_id: str) -> float:
    inv = await _raw_db.inventory.find_one(
        {"branch_id": branch_id, "product_id": product_id}, {"_id": 0, "quantity": 1}
    )
    return float(inv["quantity"]) if inv else 0.0


async def snapshot_customer(customer_id: str) -> dict:
    return await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0}) or {}


async def snapshot_invoice(invoice_id: str) -> dict:
    return await _raw_db.invoices.find_one({"id": invoice_id}, {"_id": 0}) or {}


async def snapshot_wallet(branch_id: str, wallet_type: str) -> float:
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": wallet_type}, {"_id": 0, "balance": 1}
    )
    return float(w["balance"]) if w else 0.0


async def count_movements(branch_id: str, product_id: str, ref_doc: str = "") -> int:
    q = {"branch_id": branch_id, "product_id": product_id}
    if ref_doc:
        q["reference_doc"] = ref_doc
    return await _raw_db.stock_movements.count_documents(q)


def base_sale_payload(*, branch_id: str, product_id: str, qty: float = 5, rate: float = 100):
    """Common shape for /unified-sale POST body."""
    return {
        "branch_id": branch_id,
        "items": [{
            "product_id": product_id, "quantity": qty, "rate": rate,
            "discount_type": "amount", "discount_value": 0,
        }],
        "subtotal": qty * rate,
        "freight": 0,
        "overall_discount": 0,
        "grand_total": qty * rate,
        "payment_method": "Cash",
    }
