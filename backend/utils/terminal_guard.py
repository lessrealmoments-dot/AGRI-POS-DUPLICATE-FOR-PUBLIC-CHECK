"""
Terminal-session enforcement for sensitive write endpoints.

Used by endpoints that must only run when the caller is an active paired
AgriBooks terminal app — never from a regular web browser, even with admin
credentials. This protects against:

  • A web admin (or anyone who steals their token) calling
    correct-incomplete-stock / returns / receive-payment / pickup-sms
    directly via curl/Postman without going through the terminal app.
  • A regular phone camera scanning a QR and bypassing the terminal-only UI
    gates (which can only protect the *visibility*, not the actual endpoint).

The dependency expects `terminal_id` (and optionally `device_id`) in either
the request body (preferred) or headers `X-Terminal-Id` / `X-Device-Id`.
It defers to the existing `_verify_terminal_session()` in qr_actions for
the actual DB lookup + device-binding check, so the security policy stays
in one place.
"""
from fastapi import HTTPException, Request


async def _extract_terminal_credentials(request: Request) -> tuple[str, str]:
    """Pull (terminal_id, device_id) from headers first, then JSON body.

    Headers are preferred because reading the body here doesn't interfere
    with downstream Pydantic body parsing on the route function.
    """
    terminal_id = (request.headers.get("X-Terminal-Id") or "").strip()
    device_id = (request.headers.get("X-Device-Id") or "").strip()

    if not terminal_id:
        # Fall back to body — but cache it on the request so downstream
        # Pydantic parsing can reuse the same bytes (Starlette caches
        # `request._body` after the first `.body()` call).
        try:
            raw = await request.body()
            if raw:
                import json
                body = json.loads(raw.decode("utf-8") or "{}")
                if isinstance(body, dict):
                    terminal_id = (body.get("terminal_id") or "").strip()
                    if not device_id:
                        device_id = (body.get("device_id") or "").strip()
        except (ValueError, UnicodeDecodeError):
            pass

    return terminal_id, device_id


async def require_terminal_session(request: Request):
    """
    FastAPI dependency: rejects the request with 403 unless an active paired
    terminal session is present in the body or headers.

    Returns the verified terminal_id + device_id (so endpoints can log them).
    """
    terminal_id, device_id = await _extract_terminal_credentials(request)

    if not terminal_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "This action is restricted to the AgriBooks terminal app. "
                "Web/camera-scan access is not permitted. Please open the "
                "AgriBooks app on a paired device."
            ),
        )

    # Defer to the canonical verifier (handles expired sessions, device binding).
    from routes.qr_actions import _verify_terminal_session
    await _verify_terminal_session(terminal_id, device_id)

    return {"terminal_id": terminal_id, "device_id": device_id}
