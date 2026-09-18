import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AisandboxAuthError, AuthMissingError


def _make_client() -> FlowApiClient:
    # Construct without entering the async context (no browser launched).
    return FlowApiClient.__new__(FlowApiClient)


class _FakeApiResp:
    def __init__(self, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeCtxRequest:
    def __init__(self, resp: _FakeApiResp) -> None:
        self._resp = resp
        self.calls: list[str] = []

    async def get(self, url: str) -> _FakeApiResp:
        self.calls.append(url)
        return self._resp


class _FakeContext:
    def __init__(self, resp: _FakeApiResp) -> None:
        self.request = _FakeCtxRequest(resp)


@pytest.mark.unit
def test_is_aisandbox_url_discriminates_host():
    c = _make_client()
    assert c._is_aisandbox_url("https://aisandbox-pa.googleapis.com/v1/flow/x")
    assert not c._is_aisandbox_url("https://labs.google/fx/api/trpc/project.createProject")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_builds_bearer_from_cached_token():
    c = _make_client()
    c._access_token = "ya29.FAKE"
    c._access_token_exp = 9_999_999_999.0
    headers = await c._aisandbox_auth_headers()
    assert headers["authorization"] == "Bearer ya29.FAKE"
    assert headers["origin"] == "https://labs.google"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_uses_context_request_not_a_page():
    """Regression: token fetch must use self._context.request, never a Page.

    A nested ``_checkout_page`` inside a ``_post_json`` attempt() deadlocks a
    size-1 pool (live-smoke incident 2026-05-31).
    """
    c = _make_client()
    c._access_token = None
    c._access_token_exp = 0.0
    c._context = _FakeContext(
        _FakeApiResp(200, '{"access_token":"ya29.CTX","expires":"2999-01-01T00:00:00Z"}')
    )

    def _boom():
        raise AssertionError("_checkout_page must NOT be called from token fetch")

    c._checkout_page = _boom  # type: ignore[method-assign]
    assert await c._ensure_access_token() == "ya29.CTX"
    assert c._context.request.calls == ["https://labs.google/fx/api/auth/session"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_access_token_reuses_unexpired_cache(monkeypatch):
    c = _make_client()
    c._access_token = "ya29.CACHED"
    c._access_token_exp = 9_999_999_999.0

    async def _boom():
        raise AssertionError("must not re-fetch a still-valid cached token")

    monkeypatch.setattr(c, "_fetch_access_token", _boom)
    assert await c._ensure_access_token() == "ya29.CACHED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_access_token_refetches_when_expired(monkeypatch):
    c = _make_client()
    c._access_token = "ya29.OLD"
    c._access_token_exp = 1.0  # long past → expired

    async def fake_fetch():
        return ("ya29.NEW", 9_999_999_999.0)

    monkeypatch.setattr(c, "_fetch_access_token", fake_fetch)
    assert await c._ensure_access_token() == "ya29.NEW"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_raises_when_no_token_in_session():
    c = _make_client()
    c._context = _FakeContext(_FakeApiResp(200, '{"user":{"email":"x@y.z"}}'))
    with pytest.raises(AisandboxAuthError):
        await c._fetch_access_token()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_raises_without_context():
    c = _make_client()
    c._context = None
    with pytest.raises(AuthMissingError):
        await c._fetch_access_token()


class _SeqCtxRequest:
    """Returns the scripted session bodies in order (sticky last)."""

    def __init__(self, bodies: list[str]) -> None:
        self._bodies = bodies
        self.calls = 0

    async def get(self, url: str) -> _FakeApiResp:
        body = self._bodies[min(self.calls, len(self._bodies) - 1)]
        self.calls += 1
        return _FakeApiResp(200, body)


class _SeqContext:
    def __init__(self, bodies: list[str]) -> None:
        self.request = _SeqCtxRequest(bodies)


_NO_TOKEN = '{"user":{"email":"x@y.z"}}'
_TOKEN = '{"access_token":"ya29.FRESH","expires":"2999-01-01T00:00:00Z"}'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_token_triggers_one_silent_relogin(monkeypatch, tmp_path):
    """Expired NextAuth session + live Google SSO → re-mint, then return the token."""
    c = _make_client()
    c.profile_dir = tmp_path
    c._context = _SeqContext(["{}", _TOKEN])
    calls: list[object] = []

    async def fake_refresh(ctx):
        calls.append(ctx)
        return True

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    token, _exp = await c._fetch_access_token()
    assert token == "ya29.FRESH"
    assert calls == [c._context]
    assert c._context.request.calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_relogin_still_raises_exit_3(monkeypatch):
    c = _make_client()
    c._context = _SeqContext([_NO_TOKEN])

    async def fake_refresh(ctx):
        return False

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    with pytest.raises(AisandboxAuthError):
        await c._fetch_access_token()
    # No re-read after a refresh that did not land back on Flow.
    assert c._context.request.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_relogin_is_attempted_once_per_client(monkeypatch):
    c = _make_client()
    c._context = _SeqContext([_NO_TOKEN])
    calls: list[object] = []

    async def fake_refresh(ctx):
        calls.append(ctx)
        return True

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    for _ in range(2):
        with pytest.raises(AisandboxAuthError):
            await c._fetch_access_token()
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_misses_share_one_relogin(monkeypatch, tmp_path):
    """Pooled pages missing the token together must not each start a sign-in."""
    import asyncio

    c = _make_client()
    c.profile_dir = tmp_path
    c._context = _SeqContext(["{}", "{}", _TOKEN])
    calls: list[object] = []

    async def fake_refresh(ctx):
        calls.append(ctx)
        await asyncio.sleep(0.01)
        return True

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    results = await asyncio.gather(c._fetch_access_token(), c._fetch_access_token())
    assert [t for t, _ in results] == ["ya29.FRESH", "ya29.FRESH"]
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_json_expired_session_still_gets_the_relogin(monkeypatch, tmp_path):
    """An expired session answered with an HTML page must not skip the refresh."""
    c = _make_client()
    c.profile_dir = tmp_path
    c._context = _SeqContext(["<html>sign in</html>", _TOKEN])
    calls: list[object] = []

    async def fake_refresh(ctx):
        calls.append(ctx)
        return True

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    token, _exp = await c._fetch_access_token()
    assert token == "ya29.FRESH"
    assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_json_session_with_failed_relogin_raises(monkeypatch):
    c = _make_client()
    c._context = _SeqContext(["<html>sign in</html>"])

    async def fake_refresh(ctx):
        return False

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    with pytest.raises(AisandboxAuthError, match="non-JSON"):
        await c._fetch_access_token()


_LAPSED = (
    '{"user":{"email":"x@y.z"},"access_token":"ya29.STALE",'
    '"expires":"2000-01-01T00:00:00Z","error":"ACCESS_TOKEN_REFRESH_NEEDED"}'
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lapsed_session_with_stale_token_is_refreshed(monkeypatch, tmp_path):
    """Measured 2026-09-18: a lapsed session still carries its old token — refresh anyway."""
    from gflow_cli.auth.verification import read_session_expiry

    c = _make_client()
    c.profile_dir = tmp_path
    c._context = _SeqContext([_LAPSED, _TOKEN])
    calls: list[object] = []

    async def fake_refresh(ctx):
        calls.append(ctx)
        return True

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    token, _exp = await c._fetch_access_token()
    assert token == "ya29.FRESH"
    assert len(calls) == 1
    # The fresh deadline replaces the cached one, so the pre-flight stops warning.
    cached = read_session_expiry(tmp_path)
    assert cached is not None
    assert cached.year == 2999


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lapsed_session_with_failed_refresh_raises(monkeypatch, tmp_path):
    c = _make_client()
    c.profile_dir = tmp_path
    c._context = _SeqContext([_LAPSED])

    async def fake_refresh(ctx):
        return False

    monkeypatch.setattr("gflow_cli.api.client.refresh_flow_session", fake_refresh)
    with pytest.raises(AisandboxAuthError):
        await c._fetch_access_token()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached", "expect_fetch"),
    [("2000-01-01T00:00:00+00:00", True), ("2999-01-01T00:00:00+00:00", False), (None, False)],
)
async def test_revive_lapsed_session_only_when_cache_says_lapsed(
    monkeypatch, tmp_path, cached, expect_fetch
):
    from gflow_cli.auth.verification import SESSION_EXPIRY_FILE

    if cached is not None:
        (tmp_path / SESSION_EXPIRY_FILE).write_text(cached, encoding="utf-8")
    c = _make_client()
    c.profile_dir = tmp_path
    calls: list[int] = []

    async def fake_ensure():
        calls.append(1)
        return "ya29.X"

    monkeypatch.setattr(c, "_ensure_access_token", fake_ensure)
    await c._revive_lapsed_session()
    assert bool(calls) is expect_fetch


@pytest.mark.unit
@pytest.mark.asyncio
async def test_revive_lapsed_session_never_raises(monkeypatch, tmp_path):
    from gflow_cli.auth.verification import SESSION_EXPIRY_FILE

    (tmp_path / SESSION_EXPIRY_FILE).write_text("2000-01-01T00:00:00+00:00", encoding="utf-8")
    c = _make_client()
    c.profile_dir = tmp_path

    async def fake_ensure():
        raise AisandboxAuthError(detail="still lapsed")

    monkeypatch.setattr(c, "_ensure_access_token", fake_ensure)
    await c._revive_lapsed_session()  # logs, does not raise
