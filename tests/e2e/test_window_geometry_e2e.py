"""E2E: the headed generation window reports a geometry a real browser can have.

Playwright's ``viewport=`` emulation rewrites ``screen.*`` to the viewport while
the OS window keeps its own size, so the page measured ``outerHeight`` 851 on a
720-tall ``screen`` -- a window bigger than its screen, with no room for the
toolbar. Any fingerprinting script reads that. Both generation launch sites now
size the real window instead (``GENERATION_WINDOW_SIZE_ARG``, ``no_viewport``).

$0 -- loads Flow and reads ``window``/``screen`` from the live page; no submit::

    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e tests/e2e/test_window_geometry_e2e.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import Page

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_GEOMETRY_JS = (
    "({sw: screen.width, sh: screen.height, ow: outerWidth, oh: outerHeight,"
    " iw: innerWidth, ih: innerHeight})"
)


async def _assert_real_window_geometry(page: Page) -> None:
    g: dict[str, int] = await page.evaluate(_GEOMETRY_JS)
    assert g["ow"] <= g["sw"], g
    assert g["oh"] <= g["sh"], g
    # The toolbar sits between the outer and the inner height.
    assert g["ih"] < g["oh"], g
    # Not emulated: the page is the real window's content area, not 1280x720.
    assert (g["iw"], g["ih"]) != (1280, 720), g


@pytest.mark.asyncio
async def test_e2e_client_window_geometry_is_real(e2e_profile_dir: Path, tmp_path: Path) -> None:
    async with FlowApiClient(profile_dir=e2e_profile_dir, out_dir=tmp_path) as client:
        page = client._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        await _assert_real_window_geometry(page)


@pytest.mark.asyncio
async def test_e2e_ui_automation_window_geometry_is_real(e2e_profile_dir: Path) -> None:
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        await _assert_real_window_geometry(page)
    finally:
        await transport.teardown()
