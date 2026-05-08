"""
Iter 253 — Z-Report SMS Share Links (Feb 2026).

Tokenized public access to a single closing's Z-Report. Each recipient of the
`zreport_finalized` SMS gets their own short link:

    https://<host>/zr/<token>

Tapping the link opens a mobile-first viewer + Download Detailed PDF button.
No login required — the unguessable token IS the access credential.

Security model:
  • 32-char URL-safe token, recipient-bound at SMS send time.
  • Scoped to ONE closing (one branch, one date) — not a portal.
  • 30-day expiration default; revocable.
  • Anomaly auto-revoke at 5 unique IPs (with owner SMS alert) to limit
    blast radius if the SMS is forwarded.
  • Every access logged to zreport_share_access_log.
  • PDF carries a "Sent to <Recipient> · Confidential" header for soft
    deterrent against forwarding.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import db, _raw_db, set_org_context
from utils import new_id, now_iso
from utils.auth import get_current_user

router = APIRouter(tags=["Z-Report Share Links"])
log = logging.getLogger("zreport_share")

LINK_EXPIRY_DAYS = 30
ANOMALY_UNIQUE_IP_THRESHOLD = 5
# Shorter tokens reduce SMS-filter false positives (PH carriers heuristically
# treat 32+ char random strings in SMS as phishing). 12 bytes → 16 URL-safe
# chars is still 96 bits of entropy — way more than needed for a 30-day
# revocable link. Existing 32-char tokens issued before this change keep
# working; only new mints are shorter.
TOKEN_BYTES = 12  # → 16 URL-safe chars


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _make_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def create_share_link(
    *,
    closing_id: str,
    branch_id: str,
    date: str,
    organization_id: str,
    recipient_user_id: str,
    recipient_name: str,
    recipient_phone: str = "",
    recipient_role: str = "",
) -> str:
    """Mint a fresh share token for one recipient + return the URL token.

    Idempotent within a single (closing_id, recipient_user_id) tuple — if a
    link already exists and is not revoked/expired, returns the existing one.
    """
    existing = await _raw_db.zreport_share_links.find_one({
        "closing_id": closing_id,
        "recipient_user_id": recipient_user_id,
        "revoked": {"$ne": True},
        "expires_at": {"$gt": now_iso()},
    }, {"_id": 0, "token": 1})
    if existing and existing.get("token"):
        return existing["token"]

    token = _make_token()
    expires_at = (datetime.now(timezone.utc)
                  + timedelta(days=LINK_EXPIRY_DAYS)).isoformat()
    await _raw_db.zreport_share_links.insert_one({
        "id": new_id(),
        "token": token,
        "closing_id": closing_id,
        "branch_id": branch_id,
        "organization_id": organization_id,
        "date": date,
        "recipient_user_id": recipient_user_id,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "recipient_role": recipient_role,
        "created_at": now_iso(),
        "expires_at": expires_at,
        "revoked": False,
        "access_count": 0,
        "unique_ips": [],
        "last_accessed_at": None,
    })
    return token


async def _resolve_token(token: str, request: Request) -> dict:
    """Look up token, validate not-revoked / not-expired, log access, and
    auto-revoke + alert if unique-IP threshold is exceeded.
    Returns the share-link doc for downstream use; raises HTTPException on
    invalid state.
    """
    link = await _raw_db.zreport_share_links.find_one({"token": token}, {"_id": 0})
    if not link:
        raise HTTPException(404, "Z-Report share link not found")
    if link.get("revoked"):
        raise HTTPException(410, "This Z-Report share link has been revoked")
    if link.get("expires_at") and link["expires_at"] < now_iso():
        raise HTTPException(410, "This Z-Report share link has expired")

    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")[:200]

    # Track unique IPs and access count
    unique_ips = list(set((link.get("unique_ips") or []) + [ip]))
    new_count = int(link.get("access_count", 0)) + 1
    update = {
        "$set": {
            "unique_ips": unique_ips,
            "access_count": new_count,
            "last_accessed_at": now_iso(),
        }
    }

    # Anomaly auto-revoke (Iter 253: threshold = 5 unique IPs per token)
    auto_revoked = False
    if len(unique_ips) > ANOMALY_UNIQUE_IP_THRESHOLD and not link.get("revoked"):
        update["$set"]["revoked"] = True
        update["$set"]["revoke_reason"] = (
            f"auto: exceeded {ANOMALY_UNIQUE_IP_THRESHOLD} unique IPs "
            f"(possible SMS forwarding)"
        )
        update["$set"]["revoked_at"] = now_iso()
        auto_revoked = True
    await _raw_db.zreport_share_links.update_one({"token": token}, update)

    # Append to access log
    try:
        await _raw_db.zreport_share_access_log.insert_one({
            "id": new_id(),
            "token": token,
            "closing_id": link.get("closing_id"),
            "branch_id": link.get("branch_id"),
            "organization_id": link.get("organization_id"),
            "ip": ip,
            "user_agent": ua,
            "accessed_at": now_iso(),
            "auto_revoked": auto_revoked,
        })
    except Exception:
        pass

    # Fire owner-alert SMS on auto-revoke (best-effort; don't fail the request)
    if auto_revoked:
        try:
            from routes.sms import queue_sms
            from routes.close_reminder import _resolve_recipients
            owners = await _resolve_recipients(
                link.get("organization_id", ""), link.get("branch_id", ""),
                ["owner"],
            )
            for o in owners:
                await queue_sms(
                    template_key="zreport_share_auto_revoked",
                    customer_id=o["id"],
                    customer_name=o["name"],
                    phone=o["phone"],
                    variables={
                        "branch_id": link.get("branch_id", ""),
                        "date": link.get("date", ""),
                        "recipient": link.get("recipient_name", ""),
                        "ips": str(len(unique_ips)),
                    },
                    organization_id=link.get("organization_id", ""),
                    branch_id=link.get("branch_id", ""),
                    branch_name="",
                    trigger="auto",
                    trigger_ref=f"zr-revoke:{token[:8]}",
                    dedup_key=f"zr-revoke:{token}",
                )
        except Exception as e:
            log.error(f"Owner alert dispatch failed: {e}")
        # Even though we just auto-revoked, this very access still served the
        # request (the threshold was exceeded WITH this access). Subsequent
        # taps will hit the revoked branch above.

    return link


async def _build_zreport_view(closing_id: str, branch_id: str,
                              date: str, organization_id: str) -> dict:
    """Aggregate the on-screen Z-Report shape from `daily_closings` (sealed
    record). Falls back to live preview only when no closing exists."""
    closing = await _raw_db.daily_closings.find_one(
        {"id": closing_id} if closing_id else
        {"branch_id": branch_id, "date": date, "status": "closed"},
        {"_id": 0},
    )
    branch = await _raw_db.branches.find_one(
        {"id": branch_id}, {"_id": 0, "name": 1, "branch_code": 1}
    ) or {}
    org = await _raw_db.organizations.find_one(
        {"id": organization_id}, {"_id": 0, "name": 1, "company_name": 1}
    ) or {}
    settings = await _raw_db.settings.find_one(
        {"organization_id": organization_id}, {"_id": 0, "company_info": 1}
    ) or {}
    company_name = (
        (settings.get("company_info") or {}).get("name")
        or org.get("company_name") or org.get("name") or ""
    )
    return {
        "branch_name": branch.get("name", branch_id),
        "branch_code": branch.get("branch_code", ""),
        "company_name": company_name,
        "date": date,
        "closing": closing,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public endpoints — token IS the auth, no login required
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/zreport-share/{token}")
async def get_zreport_share(token: str, request: Request):
    """Public view payload for the mobile-first Z-Report page."""
    link = await _resolve_token(token, request)
    if link.get("revoked"):
        raise HTTPException(410, "This Z-Report share link has been revoked")
    view = await _build_zreport_view(
        link.get("closing_id", ""),
        link.get("branch_id", ""),
        link.get("date", ""),
        link.get("organization_id", ""),
    )
    return {
        "token": token,
        "branch_id": link["branch_id"],
        "branch_name": view["branch_name"],
        "branch_code": view["branch_code"],
        "company_name": view["company_name"],
        "date": link["date"],
        "recipient_name": link.get("recipient_name", ""),
        "recipient_role": link.get("recipient_role", ""),
        "closing": view["closing"],
        "expires_at": link.get("expires_at"),
        "is_open": view["closing"] is None,
    }


@router.get("/zreport-share/{token}/pdf")
async def download_zreport_share_pdf(token: str, request: Request):
    """Public PDF download — Detailed mode by default.

    Filename: 'Z-Report <Branch Name> <YYYY-MM-DD>.pdf' (spaces preserved
    per user preference Iter 253).
    """
    link = await _resolve_token(token, request)
    if link.get("revoked"):
        raise HTTPException(410, "This Z-Report share link has been revoked")

    # Reuse the existing /z-report-pdf renderer with a minimal fake user
    # context. We call it as a function — not via HTTP — so we must construct
    # a user dict that satisfies its check_perm() and downstream needs.
    branch_id = link["branch_id"]
    date = link["date"]
    closing_id = link.get("closing_id") or ""
    org_id = link.get("organization_id", "")

    # Construct a synthetic read-only user with reports.view permission so
    # generate_z_report_pdf's check_perm() passes. Tied to the recipient.
    # Permissions dict shape MUST be {module: {action: bool}} — the previous
    # `{"reports": ["view"]}` (list) crashed check_perm with
    # AttributeError: 'list' object has no attribute 'get', which surfaced
    # to the share-link viewer as a generic "Network Error" on the PDF
    # download button.
    fake_user = {
        "id": link.get("recipient_user_id", "share-link"),
        "username": link.get("recipient_name", "share-link"),
        "full_name": link.get("recipient_name", ""),
        "role": "auditor",  # least-privilege role with reports.view
        "branch_id": branch_id,
        "branch_ids": [branch_id],
        "organization_id": org_id,
        "permissions": {"reports": {"view": True}},
    }

    from routes.zreport_pdf import generate_z_report_pdf
    # The public share endpoint has no JWT, so `get_current_user` never runs
    # and the per-task org context is unset. The TenantDB wrapper then
    # injects a sentinel `organization_id` and every lookup returns nothing
    # — which previously surfaced to the user as a 404 ("Closing record not
    # found"), then as a generic "Network Error" on the share page. Bind
    # the org context from the (already-validated) share-link doc so all
    # downstream tenant-scoped reads land in the correct tenant.
    set_org_context(org_id)
    response = await generate_z_report_pdf(
        date=date, branch_id=branch_id,
        closing_id=closing_id or None,
        detailed=True, user=fake_user,
    )

    # Override filename: use SPACES (per user choice 5).
    branch_name = (await _raw_db.branches.find_one(
        {"id": branch_id}, {"_id": 0, "name": 1}
    ) or {}).get("name", branch_id)
    safe_branch = "".join(c for c in branch_name if c.isalnum() or c in (" ", "-", "_")).strip()
    filename = f"Z-Report {safe_branch} {date}.pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Zreport-Recipient"] = link.get("recipient_name", "")
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Authenticated endpoints — listing + revoke
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/zreport-share/links/list")
async def list_zreport_share_links(
    branch_id: Optional[str] = None,
    include_revoked: bool = False,
    limit: int = 100,
    user=Depends(get_current_user),
):
    """List share links for management (Audit Center → Z-Report Share Links tab)."""
    if user.get("role") not in ("admin", "owner", "manager", "auditor"):
        raise HTTPException(403, "Manager+ role required")
    q = {}
    if branch_id:
        q["branch_id"] = branch_id
    elif user.get("role") == "manager":
        bids = user.get("branch_ids") or ([user.get("branch_id")] if user.get("branch_id") else [])
        if bids:
            q["branch_id"] = {"$in": bids}
    if not include_revoked:
        q["revoked"] = {"$ne": True}
    rows = await db.zreport_share_links.find(
        q, {"_id": 0,
            # Hide the raw token from non-owners — display only first 8 chars
            }
    ).sort("created_at", -1).to_list(min(max(limit, 1), 500))
    if user.get("role") not in ("admin", "owner"):
        for r in rows:
            t = r.get("token", "")
            if t:
                r["token"] = f"{t[:8]}…{t[-4:]}"
    return {"items": rows, "count": len(rows)}


@router.post("/zreport-share/links/{token}/revoke")
async def revoke_zreport_share_link(token: str, body: dict,
                                    user=Depends(get_current_user)):
    """Revoke a share link. Admin/owner only."""
    if user.get("role") not in ("admin", "owner"):
        raise HTTPException(403, "Admin or owner role required")
    note = (body.get("note") or "").strip()
    upd = await _raw_db.zreport_share_links.update_one(
        {"token": token, "revoked": {"$ne": True}},
        {"$set": {
            "revoked": True,
            "revoke_reason": f"manual: {note or 'revoked by ' + user.get('full_name', user.get('username', ''))}",
            "revoked_at": now_iso(),
            "revoked_by_id": user.get("id", ""),
            "revoked_by_name": user.get("full_name", user.get("username", "")),
        }},
    )
    if upd.matched_count == 0:
        raise HTTPException(404, "Share link not found or already revoked")
    return {"ok": True}


@router.get("/zreport-share/links/{token}/access-log")
async def get_zreport_share_access_log(token: str,
                                       user=Depends(get_current_user)):
    """Audit trail for one share link (admin/owner only)."""
    if user.get("role") not in ("admin", "owner"):
        raise HTTPException(403, "Admin or owner role required")
    rows = await db.zreport_share_access_log.find(
        {"token": token}, {"_id": 0}
    ).sort("accessed_at", -1).to_list(500)
    return {"items": rows, "count": len(rows)}
