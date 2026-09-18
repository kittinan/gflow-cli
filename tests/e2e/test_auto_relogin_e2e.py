"""Live proof that an expired Flow session is re-minted without a click.

Simulates the field failure: the labs.google NextAuth cookie has expired while the
Google account is still signed in. Before the fix this surfaced as
``AisandboxAuthError: no access_token in /fx/api/auth/session`` (exit 3) and the user
had to click "Sign in" by hand.

**Zero credits.** Opt-in: ``-m e2e_auth`` + ``GFLOW_CLI_E2E_PROFILE``.

**Mutates the profile's cookie store**: the session cookie is cleared in the live
context. On success it is re-minted before the test ends; on failure the profile is
left signed out of Flow and needs ``gflow auth login --profile <name>``. Point
``GFLOW_CLI_E2E_PROFILE`` at a copy of a profile if that matters to you.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_expired_nextauth_session_is_reminted_silently(e2e_profile_dir: Path) -> None:
    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        ctx = client._context
        assert ctx is not None
        await ctx.clear_cookies(name="__Secure-next-auth.session-token", domain="labs.google")
        client._access_token = None
        client._access_token_exp = 0.0

        _status, before = await client._read_session(ctx)
        assert not before.get("access_token"), "cookie clear did not expire the session"

        token = await client._ensure_access_token()

        assert token, "re-minted session carries the SPA Bearer token"
        assert client._relogin_result is True
