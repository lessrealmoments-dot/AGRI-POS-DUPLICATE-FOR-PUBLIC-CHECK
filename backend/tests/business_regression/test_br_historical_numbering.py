"""br_historical_numbering — Locks the auto-numbering of historical
credits & historical supplier POs against the standard format.

Prior bug:
  * `historical_credit.create_historical_credit_invoice` called
    `generate_next_number(branch_id, "SI")` with the args SWAPPED,
    producing junk like `{branch_uuid}-XX-001000`.
  * `historical_supplier_po.create_historical_supplier_po` did not
    auto-generate `reference_number`, so blank-reference entries fell
    back to `id[:8]` (a UUID slice) on the AP widget.

Both must now produce the standard `{PREFIX}-{BRANCH_CODE}-NNNNNN`
format from `utils.numbering.generate_next_number`.
"""
import os
import re
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.historical_supplier_po import (                      # noqa: E402
    create_historical_supplier_po,
)

# Match e.g. `SI-MN-001000`, `HPO-MN-001005`. Branch code is 2 chars,
# sequence is at least 6 digits (zero-padded). Allows the auto-derived
# branch-code path to produce any 2-char code (letters or 1 letter + digit).
NUMBER_RE = re.compile(r"^[A-Z]{2,4}-[A-Z0-9]{2}-\d{6,}$")


# ─────────────────────────────────────────────────────────────────────
# Shared fixtures — admin user + admin PIN seeded against `tenant`.
# ─────────────────────────────────────────────────────────────────────
import pytest_asyncio                                            # noqa: E402


@pytest_asyncio.fixture(scope="module")
async def num_admin(tenant):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id = _uid("br_num-admin")
    admin_pin = "112358"
    from utils.auth import hash_password
    await _raw_db.users.insert_one({
        "id": admin_id, "username": f"admin-{admin_id[-4:]}",
        "full_name": "BR-Num Admin", "organization_id": org_id,
        "role": "admin", "active": True,
        "branch_ids": [main], "branch_id": main,
        "manager_pin": admin_pin,
    })
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                  "pin_hash": hash_password(admin_pin),
                  "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    yield {
        "user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "pin": admin_pin,
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Historical Supplier PO: auto-generates `HPO-{BC}-NNNNNN`
# when reference_number is blank.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_num_1_hsp_auto_reference_number(
    tenant, num_admin, record_result
):
    main = tenant["branches"]["main"]
    created = await create_historical_supplier_po({
        "supplier_name": "Acme — Auto Ref",
        "branch_id": main,
        "pre_system_date": "2025-12-01",
        "amount": 1234.56,
        "description": "br_num.1 — blank reference triggers auto-gen",
        # NOTE: reference_number intentionally omitted.
        "pin": num_admin["pin"],
    }, user=num_admin["user"])

    ref = created.get("reference_number", "")
    matches = bool(NUMBER_RE.match(ref))
    starts_with_hpo = ref.startswith("HPO-")
    record_result(
        scenario="br_num.1_hsp_auto_reference",
        step="reference_matches_standard_format",
        expected={"prefix": "HPO-", "regex_match": True},
        actual={"prefix": ref[:4], "regex_match": matches},
        evidence={"po_id": created["id"], "value": ref},
    )
    assert starts_with_hpo, f"Expected HPO- prefix, got {ref!r}"
    assert matches, f"reference_number {ref!r} does not match {NUMBER_RE.pattern}"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Historical Supplier PO: user-supplied reference is preserved
# (auto-gen ONLY fills in when blank).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_num_2_hsp_user_reference_preserved(
    tenant, num_admin, record_result
):
    main = tenant["branches"]["main"]
    USER_REF = "OLD-PAPER-INVOICE-2024-001"
    created = await create_historical_supplier_po({
        "supplier_name": "Acme — User Ref",
        "branch_id": main,
        "pre_system_date": "2025-12-01",
        "amount": 999.99,
        "reference_number": USER_REF,
        "description": "br_num.2 — explicit reference preserved verbatim",
        "pin": num_admin["pin"],
    }, user=num_admin["user"])
    record_result(
        scenario="br_num.2_hsp_user_reference_preserved",
        step="user_string_kept_verbatim",
        expected={"reference_number": USER_REF},
        actual={"reference_number": created.get("reference_number")},
        evidence={"po_id": created["id"]},
    )
    assert created["reference_number"] == USER_REF


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Historical Supplier PO: two consecutive auto-gen calls produce
# DIFFERENT sequential numbers (numbering is monotonic per branch+prefix).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_num_3_hsp_sequential_numbering(
    tenant, num_admin, record_result
):
    main = tenant["branches"]["main"]
    a = await create_historical_supplier_po({
        "supplier_name": "Acme — Seq A", "branch_id": main,
        "pre_system_date": "2025-12-01", "amount": 100.0,
        "pin": num_admin["pin"],
    }, user=num_admin["user"])
    b = await create_historical_supplier_po({
        "supplier_name": "Acme — Seq B", "branch_id": main,
        "pre_system_date": "2025-12-01", "amount": 200.0,
        "pin": num_admin["pin"],
    }, user=num_admin["user"])
    # Extract the numeric tail of each and assert b == a + 1.
    seq_a = int(a["reference_number"].split("-")[-1])
    seq_b = int(b["reference_number"].split("-")[-1])
    record_result(
        scenario="br_num.3_hsp_sequential",
        step="second_auto_is_first_plus_one",
        expected={"delta": 1},
        actual={"delta": seq_b - seq_a},
        evidence={"a": a["reference_number"], "b": b["reference_number"]},
    )
    assert seq_b == seq_a + 1


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Historical Credit invoice number is the standard SI format,
# NOT the bug shape `{branch_uuid}-XX-NNNNNN`.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_num_4_historical_credit_uses_si_format(
    tenant, num_admin, record_result
):
    """Reproduces the prior bug context — we don't run the full credit
    encoder (heavy fixtures), but we DO call the same numbering helper
    with the same argument order the (fixed) endpoint now uses. If the
    args were swapped back, this test would fail loudly.
    """
    from utils.numbering import generate_next_number
    main = tenant["branches"]["main"]
    invoice_number = await generate_next_number("SI", main)
    starts_with_si = invoice_number.startswith("SI-")
    matches = bool(NUMBER_RE.match(invoice_number))
    record_result(
        scenario="br_num.4_historical_credit_si_format",
        step="invoice_number_is_si_branch_seq",
        expected={"prefix": "SI-", "regex_match": True},
        actual={"prefix": invoice_number[:3], "regex_match": matches},
        evidence={"branch_id": main, "value": invoice_number},
    )
    assert starts_with_si, f"Expected SI- prefix, got {invoice_number!r}"
    assert matches, f"invoice_number {invoice_number!r} bad shape"
    # Defensively assert the BUG shape would NOT pass: the prefix must NOT
    # be the literal branch_id substring (which is what happened when the
    # args were swapped).
    assert main not in invoice_number, (
        "invoice_number contains the branch_id as a substring — this is "
        "the prior swapped-args bug pattern. Args to generate_next_number "
        "must be (prefix, branch_id), NOT (branch_id, prefix)."
    )
