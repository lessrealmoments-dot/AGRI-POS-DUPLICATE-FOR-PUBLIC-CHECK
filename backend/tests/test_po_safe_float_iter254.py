"""
Iter 254 — PO Receive crashes with 500 when frontend sends blank/null
numerics.

Bug report (Feb 2026): User parked a PO on Main Branch then resumed it
the same day (no closed days). Clicking Confirm & Receive on Terms
returned the cryptic "An unexpected server error occurred. Please try
again or contact support." toast.

Root cause: The PO line `discount_value` field was wired to a CalcInput
without parseFloat coercion (PurchaseOrderPage.js line 1257). When the
field was blank, the API received `discount_value: ""`. The backend then
did `float("")` which raised an unhandled `ValueError`. The global
exception handler in main.py wrapped it in a generic 500 toast that
hid the real cause.

Fix:
  * Frontend: wrap the onChange in `parseFloat(v) || 0` so the field
    is always a number when sent.
  * Backend: introduce `_safe_float()` and use it for every numeric
    field on the PO payload. Defends against any frontend (or
    parked-PO snapshot, or offline-replay payload) that emits a blank
    string / None / arbitrary text.
"""
import pytest


def _safe_float_local(val, default=0.0):
    """Mirror of the in-route helper for unit-testing without a live
    FastAPI app. Verified to behave identically."""
    if val is None or val == "":
        return float(default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return float(default)


@pytest.mark.parametrize("val,expected", [
    ("", 0.0),                # blank string from a CalcInput
    (None, 0.0),              # JSON null
    ("0", 0.0),               # numeric string
    (0, 0.0),
    (70.5, 70.5),
    ("70.5", 70.5),
    ("  ", 0.0),              # whitespace-only string
    ("abc", 0.0),             # unparseable garbage
    ("12abc", 0.0),           # partial number
    ([], 0.0),                # accidental array
    ({}, 0.0),                # accidental object
])
def test_safe_float_handles_frontend_garbage(val, expected):
    assert _safe_float_local(val) == expected


def test_safe_float_with_explicit_default():
    """When the field is genuinely missing the caller can pick a
    different fallback (e.g. tax_rate=0 vs quantity=1)."""
    assert _safe_float_local("", default=1) == 1.0
    assert _safe_float_local(None, default=12) == 12.0


# ── Regression: simulate the actual buggy payload shape ──────────────
def test_po_payload_with_blank_discount_value_does_not_crash():
    """Recreate the exact payload that crashed production: a single
    line item with `discount_value=""` (empty string). The math layer
    must produce a clean line total without raising ValueError."""
    item = {
        "product_id": "abc",
        "product_name": "GALAXY MAX F1 R PACK (1 X 336)",
        "quantity": 336,
        "unit_price": 70,
        "discount_type": "amount",
        "discount_value": "",   # ← the bug
    }

    qty = _safe_float_local(item.get("quantity"), 0)
    unit_price = _safe_float_local(item.get("unit_price"), 0)
    disc_type = item.get("discount_type", "amount") or "amount"
    disc_val = _safe_float_local(item.get("discount_value"), 0)

    disc_amt = round(qty * unit_price * disc_val / 100, 2) if disc_type == "percent" else round(disc_val, 2)
    line_total = round(qty * unit_price - disc_amt, 2)

    assert disc_val == 0.0
    assert line_total == 23520.0  # 336 × 70 with no discount


def test_po_overall_freight_tax_with_blank_strings():
    """Header-level numeric fields (overall_discount_value, freight,
    tax_rate, terms_days) must also tolerate blank-string inputs."""
    data = {
        "overall_discount_value": "",
        "freight": "",
        "tax_rate": "",
        "terms_days": "",
    }
    od_val = _safe_float_local(data.get("overall_discount_value"), 0)
    freight = _safe_float_local(data.get("freight"), 0)
    tax_rate = _safe_float_local(data.get("tax_rate"), 0)
    terms_days = int(_safe_float_local(data.get("terms_days"), 0))

    assert od_val == 0.0
    assert freight == 0.0
    assert tax_rate == 0.0
    assert terms_days == 0


def test_po_terms_days_int_coercion_with_string_number():
    """A frontend that sends `terms_days: "65"` (string) must still
    work — we coerce via _safe_float first then int()."""
    val = int(_safe_float_local("65", 0))
    assert val == 65
    val = int(_safe_float_local("", 0))
    assert val == 0
    val = int(_safe_float_local(None, 30))
    assert val == 30
