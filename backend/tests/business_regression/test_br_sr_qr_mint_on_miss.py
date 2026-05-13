"""br_sr_qr_mint — Mint-on-miss QR code for legacy stock-request POs.

Locks the FE behaviour shipped with this fork: when an older branch-request
PO lacks a `doc_code` row (created before auto-doc-code generation was
wired in at PO creation), the View-QR dialog falls back to
`POST /api/doc/generate-code` and the PO becomes scannable, with
`confirm_stock_request` present in `view_document_open(code).available_actions`.

This is the regression net for the live-site recovery flow used to fix
PO-SB-001005-class POs without touching production data.
"""
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.business_regression._fixtures import seed_product    # noqa: E402
from tests.phase2b._fixtures import _uid                         # noqa: E402
from routes.purchase_orders import create_purchase_order         # noqa: E402
from routes.doc_lookup import generate_doc_code, view_document_open  # noqa: E402


async def _request_payload(requesting_branch_id, supply_branch_id,
                           product_id, product_name, qty=10):
    return {
        "po_type": "branch_request",
        "branch_id": requesting_branch_id,
        "supply_branch_id": supply_branch_id,
        "vendor": "Internal — Branch Stock Request",
        "items": [{
            "product_id": product_id, "product_name": product_name,
            "unit": "pc", "quantity": qty, "unit_price": 60.0,
        }],
        "show_retail": True,
        "notes": "br_sr_qr_mint — legacy QR mint test",
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Legacy PO (no doc_code) becomes scannable after generate-code
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sr_qr_mint_1_legacy_po_becomes_scannable(
    tenant, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    owner = tenant["users"]["owner"]

    pid = await seed_product(
        org_id, main, name=f"br_sr_qr_mint-{_uid('p')[-4:]}",
        price=100, stock=50, cost=60,
    )
    po = await create_purchase_order(
        await _request_payload(b2, main, pid, "QR Mint Product"),
        user=owner,
    )
    po_id = po["id"]

    # Simulate the legacy state: strip the doc_code from BOTH the
    # `doc_codes` row and the PO row, so the dialog's first lookup
    # returns empty (mirrors what PO-SB-001005 looks like in prod).
    await _raw_db.doc_codes.delete_many(
        {"doc_type": "purchase_order", "doc_id": po_id}
    )
    await _raw_db.purchase_orders.update_one(
        {"id": po_id}, {"$unset": {"doc_code": ""}}
    )
    # Sanity: no code on the PO at this point.
    legacy = await _raw_db.purchase_orders.find_one(
        {"id": po_id}, {"_id": 0, "doc_code": 1}
    )
    assert "doc_code" not in legacy or not legacy.get("doc_code")

    # FE dialog fallback path: mint a code on demand.
    mint = await generate_doc_code(
        {"doc_type": "purchase_order", "doc_id": po_id},
        user=owner,
    )
    code = mint["code"]
    assert code, "generate-code must return a non-empty code"

    # Scan the freshly minted code via the public viewer (no PIN).
    view = await view_document_open(code)
    record_result(
        scenario="br_sr_qr_mint.1_legacy_po_becomes_scannable",
        step="confirm_action_available_after_mint",
        expected={
            "doc_type": "purchase_order",
            "is_branch_request": True,
            "confirm_action_listed": True,
        },
        actual={
            "doc_type": view["doc_type"],
            "is_branch_request": view["is_branch_request"],
            "confirm_action_listed":
                "confirm_stock_request" in view["available_actions"],
        },
        evidence={"po_id": po_id, "code": code},
    )
    assert view["doc_type"] == "purchase_order"
    assert view["is_branch_request"] is True
    assert "confirm_stock_request" in view["available_actions"]


# ═════════════════════════════════════════════════════════════════════
# Test 2 — generate-code is idempotent (second call returns same code)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sr_qr_mint_2_generate_code_is_idempotent(
    tenant, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    owner = tenant["users"]["owner"]

    pid = await seed_product(
        org_id, main, name=f"br_sr_qr_mint2-{_uid('p')[-4:]}",
        price=100, stock=50, cost=60,
    )
    po = await create_purchase_order(
        await _request_payload(b2, main, pid, "QR Mint Idemp"),
        user=owner,
    )
    first = await generate_doc_code(
        {"doc_type": "purchase_order", "doc_id": po["id"]}, user=owner,
    )
    second = await generate_doc_code(
        {"doc_type": "purchase_order", "doc_id": po["id"]}, user=owner,
    )
    record_result(
        scenario="br_sr_qr_mint.2_idempotent",
        step="second_mint_returns_same_code",
        expected={"same_code": True},
        actual={"same_code": first["code"] == second["code"]},
        evidence={"po_id": po["id"],
                  "first": first["code"], "second": second["code"]},
    )
    assert first["code"] == second["code"]


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Minted code on a PO with a linked BTO does NOT list
#          confirm_stock_request (still respects the lockout)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sr_qr_mint_3_linked_bto_still_blocks_confirm(
    tenant, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    owner = tenant["users"]["owner"]

    pid = await seed_product(
        org_id, main, name=f"br_sr_qr_mint3-{_uid('p')[-4:]}",
        price=100, stock=50, cost=60,
    )
    po = await create_purchase_order(
        await _request_payload(b2, main, pid, "QR Mint Locked"),
        user=owner,
    )
    po_id = po["id"]
    # Simulate a linked BTO (no need to run the real generator — only the
    # link is checked by view_document_open).
    await _raw_db.branch_transfer_orders.insert_one({
        "id": _uid("bto"), "organization_id": org_id,
        "order_number": "BTO-FAKE-LOCK", "request_po_id": po_id,
        "from_branch_id": main, "to_branch_id": b2,
        "status": "sent", "items": [], "created_at": "2026-02-13T00:00:00Z",
    })

    mint = await generate_doc_code(
        {"doc_type": "purchase_order", "doc_id": po_id}, user=owner,
    )
    view = await view_document_open(mint["code"])
    record_result(
        scenario="br_sr_qr_mint.3_linked_bto_blocks",
        step="confirm_action_omitted_when_linked_bto_exists",
        expected={"confirm_listed": False, "has_linked_bto": True},
        actual={
            "confirm_listed": "confirm_stock_request" in view["available_actions"],
            "has_linked_bto": view.get("has_linked_bto", False),
        },
        evidence={"po_id": po_id, "code": mint["code"]},
    )
    assert "confirm_stock_request" not in view["available_actions"]
    assert view["has_linked_bto"] is True
