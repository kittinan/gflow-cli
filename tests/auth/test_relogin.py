"""Unit tests for the selector-free Flow session refresh (auth/relogin.py)."""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.auth.relogin import refresh_flow_session

_OAUTH = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y"


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Request:
    def __init__(self, csrf: _Resp | Exception, signin: _Resp | Exception) -> None:
        self._csrf = csrf
        self._signin = signin
        self.posts: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str) -> _Resp:
        assert url.endswith("/fx/api/auth/csrf")
        if isinstance(self._csrf, Exception):
            raise self._csrf
        return self._csrf

    async def post(self, url: str, *, form: dict[str, str], max_redirects: int) -> _Resp:
        self.posts.append((url, form))
        if isinstance(self._signin, Exception):
            raise self._signin
        return self._signin


class _Page:
    """Page whose URL walks a scripted sequence, one step per read after goto."""

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls
        self._i = 0
        self.gotos: list[str] = []
        self.closed = False

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)

    @property
    def url(self) -> str:
        u = self._urls[min(self._i, len(self._urls) - 1)]
        self._i += 1
        return u

    async def close(self) -> None:
        self.closed = True


class _Ctx:
    def __init__(self, request: _Request, page: _Page) -> None:
        self.request = request
        self._page = page

    async def new_page(self) -> _Page:
        return self._page


def _ok_request() -> _Request:
    return _Request(_Resp(200, {"csrfToken": "tok"}), _Resp(200, {"url": _OAUTH}))


@pytest.mark.unit
async def test_round_trip_lands_back_on_flow() -> None:
    page = _Page([_OAUTH, "https://flow.google.com/"])
    req = _ok_request()
    assert await refresh_flow_session(_Ctx(req, page), poll_s=0) is True
    assert page.gotos == [_OAUTH]
    assert page.closed
    url, form = req.posts[0]
    assert url.endswith("/fx/api/auth/signin/google")
    assert form["csrfToken"] == "tok"
    assert form["json"] == "true"


@pytest.mark.unit
async def test_refuses_non_google_oauth_url() -> None:
    page = _Page(["about:blank"])
    req = _Request(_Resp(200, {"csrfToken": "t"}), _Resp(200, {"url": "https://evil.example/x"}))
    assert await refresh_flow_session(_Ctx(req, page), poll_s=0) is False
    assert page.gotos == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "challenge",
    [
        "https://accounts.google.com/v3/signin/challenge/pwd?x=1",
        "https://accounts.google.com/v3/signin/identifier?x=1",
        "https://accounts.google.com/signin/oauth/id?x=1",
        "https://accounts.google.com/signin/rejected",
    ],
)
async def test_human_challenge_bails_before_deadline(challenge: str) -> None:
    page = _Page([challenge])
    # A huge timeout: the test only finishes if the challenge short-circuits.
    ok = await refresh_flow_session(_Ctx(_ok_request(), page), timeout_s=3600, poll_s=0)
    assert ok is False
    assert page.closed


@pytest.mark.unit
async def test_still_on_google_at_deadline_fails() -> None:
    page = _Page([_OAUTH])
    assert (
        await refresh_flow_session(_Ctx(_ok_request(), page), timeout_s=0.05, poll_s=0.01) is False
    )
    assert page.closed


@pytest.mark.unit
@pytest.mark.parametrize(
    "request_",
    [
        _Request(RuntimeError("net down"), _Resp(200, {"url": _OAUTH})),
        _Request(_Resp(500, {}), _Resp(200, {"url": _OAUTH})),
        _Request(_Resp(200, {}), _Resp(200, {"url": _OAUTH})),
        _Request(_Resp(200, {"csrfToken": "t"}), RuntimeError("boom")),
        _Request(_Resp(200, {"csrfToken": "t"}), _Resp(403, {"url": _OAUTH})),
        _Request(_Resp(200, {"csrfToken": "t"}), _Resp(200, ValueError("not json"))),
    ],
)
async def test_request_failures_return_false_never_raise(request_: _Request) -> None:
    page = _Page(["about:blank"])
    assert await refresh_flow_session(_Ctx(request_, page), poll_s=0) is False
    assert page.gotos == []


@pytest.mark.unit
async def test_new_page_failure_returns_false() -> None:
    class _NoPageCtx(_Ctx):
        async def new_page(self) -> _Page:
            raise RuntimeError("context closed")

    assert await refresh_flow_session(_NoPageCtx(_ok_request(), _Page([""])), poll_s=0) is False
