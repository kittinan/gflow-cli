#!/usr/bin/env python3
r"""T2V entity-attach repro spike — 0 credits (generate requests are ABORTED).

Reproduces the video-only Character attachment regression and proves the fix
hypothesis, against the real editor DOM, on one browser session:

Phase A (the bug, as shipped): replicate `_generate_video_locked` for a
``Mode.T2V`` request carrying ``reference_entities`` — switch to Video mode,
NO sub-mode switch (exactly what ``configure_video_settings`` does for T2V) —
then attempt `_attach_character_entities`. Record the DOM state (bare Video
tab) and the failure mode.

Phase B (the fix hypothesis): switch the SAME composer into the
'references'/ingredients sub-mode, attempt the identical attach, then submit
the prompt with the generate request intercepted + aborted. Capture the POST
body: does ``requests[].referenceEntities`` carry the entityId, and which
batchAsyncGenerateVideo* route fires?

Usage (headed, supervised):

    ! uv run python scripts/dev/spike_t2v_entity_attach_repro.py \
        --profile kittinansr2-botun \
        --project 20ed0ab0-e524-468d-8f38-3a52ab21a3ac \
        --entity-id e6dc08d2-a14a-47f3-9361-2e0f24caff95 --name botun
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    VideoGenerationMixin,
)

_GEN_ROUTE_GLOB = "**/video:batchAsyncGenerateVideo*"


async def _dom_probe(page: Any, label: str) -> dict[str, Any]:
    """Count Add-Media buttons + open dialogs — the before/after Add Media DOM."""
    return {
        "label": label,
        "add_media_count": await page.locator(ADD_MEDIA_BUTTON).count(),
        "add_media_visible": await page.locator(ADD_MEDIA_BUTTON).first.is_visible()
        if await page.locator(ADD_MEDIA_BUTTON).count()
        else False,
        "open_dialogs": await page.locator("[role='dialog']").count(),
    }


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    entity_id: str,
    name: str,
    locale: str,
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {}
    report: dict[str, Any] = {
        "spike": "t2v-entity-attach-repro",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entity_id": entity_id,
        "name": name,
        "project": project_id,
    }

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        async def _on_route(route: Any) -> None:
            req = route.request
            if not captured and "batchAsyncGenerateVideo" in req.url:
                try:
                    captured["url"] = req.url
                    captured["post_data"] = req.post_data
                except Exception as e:  # noqa: BLE001
                    captured["error"] = f"{type(e).__name__}: {e}"
            if req.method == "POST" and "aisandbox" in req.url:
                report.setdefault("posts_seen", []).append(req.url.split("?")[0][-80:])
            if "batchAsyncGenerateVideo" in req.url:
                await route.abort()  # 0 credits — never reaches Google
            else:
                await route.continue_()

        await page.route("**/aisandbox-pa.googleapis.com/**", _on_route)
        try:
            url = routes.project_editor_url(locale, project_id)
            step("0", f"goto {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")

            await VideoGenerationMixin._exit_agent_mode(page)  # noqa: SLF001
            await VideoGenerationMixin._wait_video_editor_ready(page)  # noqa: SLF001
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
            await page.wait_for_timeout(1_500)

            # ---------------- Phase A: T2V as shipped — bare Video tab --------
            report["A_dom_before"] = await _dom_probe(page, "bare-video-tab")
            await page.screenshot(path=str(out_dir / "A_bare_video_tab.png"))
            try:
                await asyncio.wait_for(
                    VideoGenerationMixin._attach_character_entities(  # noqa: SLF001
                        page, [(entity_id, name)], out_dir=out_dir
                    ),
                    timeout=25_000,
                )
                report["A_attach"] = {"outcome": "ATTACHED (unexpected)"}
            except Exception as exc:  # noqa: BLE001
                report["A_attach"] = {
                    "outcome": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}"[:600],
                }
            await page.screenshot(path=str(out_dir / "A_after_attach_attempt.png"))

            # ---------------- Submit straight from the bare Video tab ---------
            # Decisive question: with the entity staged and NO sub-mode switch,
            # does the submit carry referenceEntities, and on which route?
            if report["A_attach"]["outcome"].startswith("ATTACHED"):
                try:
                    await client.transport._send_prompt(  # noqa: SLF001
                        page, "standing in a bright modern room, waving", out_dir
                    )
                except Exception as exc:  # noqa: BLE001
                    report["A_send_prompt_error"] = f"{type(exc).__name__}: {exc}"[:600]
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and not captured:
                    await asyncio.sleep(0.3)
                await page.screenshot(path=str(out_dir / "A_after_submit.png"))
                report["A_dom_after_submit"] = await _dom_probe(page, "after-submit")
            else:
                # Close any dialog the failed attempt left open.
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(800)
        finally:
            await page.unroute("**/aisandbox-pa.googleapis.com/**", _on_route)
            client._checkin_page(page)  # noqa: SLF001

    pd = captured.get("post_data")
    report["captured"] = bool(pd)
    if pd:
        try:
            body = json.loads(pd)
            reqs = body.get("requests") or []
            ents = [
                e.get("entityId") for r in reqs for e in (r.get("referenceEntities") or [])
            ]
            report["route"] = (captured.get("url") or "").split("/")[-1]
            report["referenceEntities"] = ents
            report["request0_keys"] = sorted(reqs[0].keys()) if reqs else []
        except Exception as exc:  # noqa: BLE001
            report["parse_error"] = f"{type(exc).__name__}: {exc}"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Repro T2V entity attach failure (0 credits).")
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "default"))
    p.add_argument("--project", required=True)
    p.add_argument("--entity-id", dest="entity_id", required=True)
    p.add_argument("--name", default="botun")
    p.add_argument("--locale", default="", help="account locale segment; empty = let Flow normalise")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_t2v_entity_attach_repro", ".json")
    )
    try:
        return asyncio.run(
            _run(
                profile_dir=resolve_profile_dir(args.profile),
                project_id=args.project,
                entity_id=args.entity_id,
                name=args.name,
                locale=args.locale or None,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[spike] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
