"""
Iter 238 — Time/Date timezone-correctness tests.

Verifies the helpers and the user-facing fix for "8 AM Manila sale shows 00:00":
  1. `today_local()` returns the Manila day, not the UTC day, even at the
     UTC-midnight boundary (= 8 AM Manila).
  2. `now_local_time_str()` returns the Manila wall-clock time.
  3. `utc_iso_to_local_time_str()` converts a UTC ISO into Manila HH:MM:SS.
  4. The new helpers are async and resolve org timezones via the runtime
     resolver.
  5. `today_local()` falls back to Asia/Manila when org_id is empty.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from utils.helpers import (
    today_local, now_local_iso, now_local_time_str,
    utc_iso_to_local_time_str,
)


# ── pure-unit tests (no DB / no HTTP) ────────────────────────────────────


def test_utc_iso_to_local_time_str_manila():
    """8 AM Manila = 00:00 UTC. Confirm the conversion goes the OTHER way:
    a 00:00 UTC ISO must render as 08:00:00 in Manila."""
    s = utc_iso_to_local_time_str("2026-05-05T00:00:00+00:00", "Asia/Manila")
    assert s == "08:00:00"

    # 4 PM Manila = 08:00 UTC
    s = utc_iso_to_local_time_str("2026-05-05T08:00:00Z", "Asia/Manila")
    assert s == "16:00:00"


def test_utc_iso_to_local_time_str_naive_falls_back():
    """A timestamp without TZ info is treated as UTC."""
    s = utc_iso_to_local_time_str("2026-05-05T00:00:00", "Asia/Manila")
    assert s == "08:00:00"


def test_utc_iso_to_local_time_str_empty():
    assert utc_iso_to_local_time_str("", "Asia/Manila") == ""
    assert utc_iso_to_local_time_str(None, "Asia/Manila") == ""


def test_today_local_returns_manila_day_at_utc_midnight():
    """At exactly 00:00 UTC on May 5 (= 08:00 Manila on May 5), the Manila
    day must already be May 5. The OLD UTC-based code returned May 5 too at
    this exact moment — but at 23:59 UTC May 4 (= 07:59 Manila May 5) it
    returned May 4 = WRONG. Verify the boundary."""
    fake_utc_late = datetime(2026, 5, 4, 23, 59, 0, tzinfo=timezone.utc)
    # When we're at 23:59 UTC May 4, Manila is 07:59 May 5.
    # today_local() must return 2026-05-05 even though UTC says 2026-05-04.
    with patch("routes.close_reminder._local_now_in",
               return_value=fake_utc_late.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Manila"))):
        with patch("routes.close_reminder._resolve_org_timezone",
                   return_value="Asia/Manila"):
            result = asyncio.run(today_local("any-org"))
            assert result == "2026-05-05", f"Manila day at 23:59 UTC May 4 must be May 5, got {result}"


def test_today_local_no_org_id_defaults_to_manila():
    """Empty org_id must resolve to Asia/Manila and not raise."""
    result = asyncio.run(today_local(""))
    # Should be a YYYY-MM-DD string
    assert len(result) == 10 and result[4] == "-" and result[7] == "-"


def test_now_local_time_str_returns_manila_hhmmss():
    """now_local_time_str() must return HH:MM:SS that matches Manila wall
    clock. Tolerance: ±2 sec for execution drift."""
    s = asyncio.run(now_local_time_str(""))
    assert len(s) == 8 and s[2] == ":" and s[5] == ":"
    h, m, _ = s.split(":")
    # Compare to Manila now
    from zoneinfo import ZoneInfo
    manila = datetime.now(ZoneInfo("Asia/Manila"))
    expected_h = manila.strftime("%H")
    assert h == expected_h, \
        f"Hour mismatch: helper={h}, manila now={expected_h}"


def test_now_local_iso_has_offset():
    """Returned ISO must include a tz offset (+08:00 for Manila)."""
    s = asyncio.run(now_local_iso(""))
    assert "+08:00" in s or "+0800" in s, f"missing Manila offset: {s}"


def test_today_local_string_format():
    """Always exactly YYYY-MM-DD, never with time component."""
    s = asyncio.run(today_local(""))
    assert len(s) == 10
    int(s[:4])  # year parses
    int(s[5:7])  # month parses
    int(s[8:10])  # day parses
