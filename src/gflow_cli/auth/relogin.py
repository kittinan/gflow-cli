"""Re-mint an expired Flow (NextAuth) session while the Google account is still signed in.

The labs.google NextAuth cookie (``__Secure-next-auth.session-token``) expires on its own
schedule, independently of the Google SSO session. When only the former is gone, Flow's
"Sign in" button completes with no human input: Google sees a live SSO session and
redirects straight back to the NextAuth callback. This module performs that same round
trip without touching the DOM — NextAuth's own API returns the Google OAuth URL, and a
temporary page follows it — so it is locale-invariant and selector-free by construction.

Measured 2026-09-18 on a copy of a live profile with the NextAuth cookie (and the
flow.google.com ``OSID``) deleted: ``flow.google.com`` re-signs itself via Google's
passive login, but ``/fx/api/auth/session`` stays empty until this round trip runs,
after which it carries ``user`` and ``access_token`` again.

When Google wants a human (password, 2FA, CAPTCHA, account chooser, consent), this gives
up immediately and returns ``False``; the caller raises its usual ``AuthExpiredError``.
Nothing sensitive is logged — not the OAuth URL, the CSRF token, or any response body.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import structlog

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

logger = structlog.get_logger(__name__)

_AUTH_BASE = "https://labs.google/fx/api/auth"
_CALLBACK_URL = "https://labs.google/fx/tools/flow"
_GOOGLE_ACCOUNTS_PREFIX = "https://accounts.google.com/"
_FLOW_HOSTS = frozenset({"labs.google", "flow.google.com"})
# accounts.google.com paths that only a human can get past. Seeing one means the
# silent round trip is impossible, so waiting out the timeout would only add latency.
_HUMAN_ONLY_PATHS = (
    "/challenge",
    "/signin/rejected",
    "/v3/signin/identifier",
    "/signin/v2/identifier",
    "/signin/oauth/id",
    "/signin/oauth/consent",
    "/speedbump",
)


async def _oauth_url(ctx: Any) -> str | None:
    """Ask NextAuth for the Google OAuth URL. ``None`` on any failure."""
    csrf_resp = await ctx.request.get(f"{_AUTH_BASE}/csrf")
    if csrf_resp.status != 200:
        return None
    csrf_body: Any = await csrf_resp.json()
    csrf = (
        cast("dict[str, Any]", csrf_body).get("csrfToken") if isinstance(csrf_body, dict) else None
    )
    if not isinstance(csrf, str) or not csrf:
        return None
    signin_resp = await ctx.request.post(
        f"{_AUTH_BASE}/signin/google",
        form={"csrfToken": csrf, "callbackUrl": _CALLBACK_URL, "json": "true"},
        max_redirects=0,
    )
    if signin_resp.status != 200:
        return None
    body: Any = await signin_resp.json()
    url = cast("dict[str, Any]", body).get("url") if isinstance(body, dict) else None
    return url if isinstance(url, str) else None


async def refresh_flow_session(
    ctx: BrowserContext,
    *,
    timeout_s: float = 20.0,
    poll_s: float = 0.25,
) -> bool:
    """Run one silent NextAuth sign-in round trip. ``True`` when it landed back on Flow.

    ``True`` only means the redirect chain completed; the caller re-reads
    ``/fx/api/auth/session`` to confirm. Never raises.
    """
    try:
        url = await _oauth_url(ctx)
    except Exception as exc:  # noqa: BLE001 — a failed refresh falls back to exit 3
        logger.warning(
            "auth.flow_session_refresh",
            outcome="failed",
            stage="signin",
            error_type=type(exc).__name__,
        )
        return False
    if url is None or not url.startswith(_GOOGLE_ACCOUNTS_PREFIX):
        logger.warning("auth.flow_session_refresh", outcome="failed", stage="signin")
        return False

    try:
        page = await ctx.new_page()
    except Exception as exc:  # noqa: BLE001 — a failed refresh falls back to exit 3
        logger.warning(
            "auth.flow_session_refresh",
            outcome="failed",
            stage="page",
            error_type=type(exc).__name__,
        )
        return False
    try:
        # A redirect can abort the first navigation; the poll below decides.
        with contextlib.suppress(Exception):
            await page.goto(url, wait_until="commit", timeout=timeout_s * 1000)
        deadline = time.monotonic() + timeout_s
        while True:
            parts = urlsplit(page.url)
            if parts.hostname in _FLOW_HOSTS:
                logger.info("auth.flow_session_refresh", outcome="ok")
                return True
            if parts.hostname == "accounts.google.com" and any(
                marker in parts.path for marker in _HUMAN_ONLY_PATHS
            ):
                logger.warning("auth.flow_session_refresh", outcome="challenge")
                return False
            if time.monotonic() >= deadline:
                logger.warning("auth.flow_session_refresh", outcome="timeout")
                return False
            await asyncio.sleep(poll_s)
    finally:
        # Closing the temporary page must not mask the result.
        with contextlib.suppress(Exception):
            await page.close()
