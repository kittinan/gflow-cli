# Auto Flow Re-login Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature auto-flow-relogin` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** When the labs.google NextAuth session has expired but the Google account is still
signed in, gflow re-mints the Flow session by itself instead of failing with exit 3 and
making the user click "Sign in".

**Architecture:** New `auth/relogin.py` drives NextAuth's own sign-in API
(`GET /fx/api/auth/csrf` → `POST /fx/api/auth/signin/google` with `json=true` → navigate a
temporary page to the returned Google OAuth URL). Google, seeing a live SSO session, redirects
straight back to the callback, which sets a fresh `__Secure-next-auth.session-token`.
`FlowApiClient._fetch_access_token` calls it once when `/fx/api/auth/session` carries no
`access_token`, then re-reads the session. No DOM selectors, no new flags, no new exit codes.

**Predict verdict:** GO — confidence 7/10 (2026-09-18, this session)

**Evidence:** spike on a copy of profile `kittinansr2` with `__Secure-next-auth.session-token`
(labs.google) and `OSID` (flow.google.com) deleted. `flow.google.com` self-heals via Google's
passive login (`?pli=1`) but the labs session stays empty; the NextAuth round trip restored
`user` + `access_token` with zero clicks.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Concurrent pooled callers each start a sign-in | `asyncio.Lock` + one attempt per client lifetime |
| High | Google needs a human (password / 2FA / CAPTCHA / consent) | Bail immediately on a challenge path; hard 20 s cap; fall through to existing `AuthExpiredError` (exit 3) |
| Medium | Returned OAuth URL is not Google | Refuse anything not starting with `https://accounts.google.com/` |
| Medium | Secrets in logs | Log outcome only — never the OAuth URL, CSRF token or response body |
| Medium | Nested page checkout deadlocks a size-1 pool | Use `ctx.new_page()` (temporary), never `_checkout_page` |
| Low | Docs still claim "no auto-refresh" | Scope the claim in KNOWN_ISSUES.md + docs/AUTHENTICATION.md |

**Out of scope:** `gflow credits` (httpx-only, no browser); `drivers/agentic.py` empty-token
fallback; a proactive per-command session probe (deferred until a live run shows the UI path
needs the labs session).

---

## File structure

### New files
```
src/gflow_cli/auth/relogin.py
  refresh_flow_session(ctx) -> bool — one selector-free NextAuth sign-in round trip
tests/auth/test_relogin.py
  unit tests with a fake BrowserContext
tests/e2e/test_auto_relogin_e2e.py
  e2e_auth: clear the session cookie in-context, then fetch a token
```

### Modified files
```
src/gflow_cli/api/client.py
  _fetch_access_token: one locked refresh attempt on a missing access_token
tests/api/test_aisandbox_auth_headers.py
  refresh-then-retry, refresh-failure, single-flight tests
KNOWN_ISSUES.md, docs/AUTHENTICATION.md, CHANGELOG.md
```

---

## Task 1 — relogin helper (test first)

**Steps:**
- [x] Red tests in `tests/auth/test_relogin.py`
- [x] Implement `auth/relogin.py`

**Tests:**
- [x] happy path: csrf → signin POST → OAuth URL → lands off accounts.google.com → True; temp page closed
- [x] non-Google OAuth URL → False, page never navigated
- [x] challenge path (`/v3/signin/challenge`) → False without waiting out the timeout
- [x] csrf / signin request raises or returns non-200 → False (never raises)
- [x] still on accounts.google.com at deadline → False

## Task 2 — wire into FlowApiClient

**Steps:**
- [x] `_fetch_access_token` refreshes once on missing `access_token`, re-reads the session
- [x] `asyncio.Lock` single-flight, one attempt per client

**Tests:**
- [x] missing token → refresh ok → token returned
- [x] missing token → refresh fails → `AisandboxAuthError` (exit 3)
- [x] second miss after a failed attempt does not refresh again

## Task 3 — e2e + live verification
- [x] `tests/e2e/test_auto_relogin_e2e.py` (`e2e_auth`, zero credits)
- [x] Run it against a copy of `kittinansr2` — 1 passed, `auth.flow_session_refresh outcome=ok` (2026-09-18)

## Task 4 — docs
- [x] KNOWN_ISSUES.md "Browser session expires periodically" scoped to the full-logout case
- [x] docs/AUTHENTICATION.md § Refresh / expiry
- [x] CHANGELOG `[Unreleased]`

## Definition of done

- [x] `/gflow:check` green
- [x] e2e green against a live profile
- [x] CHANGELOG + docs updated
- [x] MCP: no surface change — tools share `FlowApiClient` (stated in PR)
