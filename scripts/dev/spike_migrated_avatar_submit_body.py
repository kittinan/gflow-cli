"""Does a likeness + a local reference survive the SAME migrated submit? — $0.

A live run attached both (`avatar_attached likeness_chips=1`,
`references_attached count=1`) and the submit body then carried the likeness ids and the
project but NOT the uploaded media id, so `_r2v_body_problem` refused it — after the click,
which bills. This reads the same body without ever creating the request, per
[[credit-free-route-abort-verification]]: `window.fetch` and `XMLHttpRequest` are patched
inside the page to record and drop anything going to `data/batchexecute`.

`--order` decides whether the reference or the avatar is attached first: the popover click
that adds the likeness also dismisses the popover, and whether it also resets the prompt's
ingredient set is exactly what this measures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    ADD_MENU_POPOVER,
    INGREDIENTS_LIGATURE,
    LIKENESS_CHIP,
    PROMPT_BOX_ADD,
    MigratedComposer,
    _ligature,
)
from gflow_cli.api.video import VideoModel  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

BLOCK_JS = """
() => {
  window.__captured = [];
  const isTarget = (u) => String(u).includes('data/batchexecute');
  const of = window.fetch;
  window.fetch = function (input, init) {
    const url = (input && input.url) || input;
    const body = (init && init.body) || (input && input.body) || null;
    if (isTarget(url)) {
      window.__captured.push({via: 'fetch', url: String(url), body: body ? String(body) : null});
      return Promise.reject(new Error('blocked by probe'));
    }
    return of.apply(this, arguments);
  };
  const oo = XMLHttpRequest.prototype.open;
  const os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    this.__url = u; return oo.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (b) {
    if (isTarget(this.__url)) {
      window.__captured.push({via: 'xhr', url: String(this.__url), body: b ? String(b) : null});
      return;
    }
    return os.apply(this, arguments);
  };
}
"""

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MODEL_KEY = re.compile(
    r"[a-z0-9]+(?:_[a-z0-9]+)*_(?:t2v|i2v|r2v)_[a-z0-9_]+|veo_[a-z0-9_]+|abra_[a-z0-9_]+"
)


async def _main(profile: str, project_id: str, ref: Path, order: str, model: str) -> int:
    report: dict[str, Any] = {"order": order, "model": model}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        await composer.ensure_editor(page, project_id)
        pane = await composer._open_pane(page)  # noqa: SLF001
        try:
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)  # noqa: SLF001
            if model:
                await composer._select_model(page, pane, VideoModel(model))  # noqa: SLF001
        finally:
            await composer._close_pane(page, strict=False)  # noqa: SLF001

        media_ids: tuple[str, ...] = ()
        if order == "popover-both":
            # The route a human actually uses: BOTH the image and the avatar come from the
            # prompt-box popover's own asset list, never from the `@` mention picker. The
            # popover has Images/Uploads tabs alongside Avatars; the driver had only ever
            # tried mentions, so every previous "Flow refuses" reading was about the
            # mention picker, not about Flow.
            media_ids = (await composer._upload_via_toolbar(page, project_id, ref),)  # noqa: SLF001
            await page.wait_for_timeout(1500)
            await page.locator(PROMPT_BOX_ADD).first.click(timeout=5000)
            await page.wait_for_timeout(2000)
            items = page.locator(f"{ADD_MENU_POPOVER} flow-add-menu-asset-item")
            report["popover_items"] = await items.count()
            picked = None
            for i in range(await items.count()):
                txt = (await items.nth(i).inner_text()).strip()
                if ref.name in txt:
                    picked = txt[:40]
                    await items.nth(i).click(timeout=5000)
                    break
            report["picked_from_popover"] = picked
            await page.wait_for_timeout(1500)
            # Clicking an asset only SELECTS it — the detail pane's action is what puts it
            # on the prompt. (The avatar differs: its click attaches AND dismisses.)
            action = page.locator(f"{ADD_MENU_POPOVER} flow-add-menu-detail-pane button").first
            report["detail_action_found"] = bool(await action.count())
            if await action.count():
                report["detail_action_text"] = (await action.inner_text()).strip()[:40]
                await action.click(timeout=5000)
                await page.wait_for_timeout(2000)
            report["chips_after_popover_pick"] = await composer.read_chips(page)
            report["popover_open_after_pick"] = bool(await page.locator(ADD_MENU_POPOVER).count())
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
            await composer.attach_avatar(page)
        elif order == "avatar-only":
            await composer.attach_avatar(page)
        elif order == "ref-first":
            media_ids = await composer.attach_references(page, project_id, (ref,))
            await composer.attach_avatar(page)
        else:
            await composer.attach_avatar(page)
            # The popover leaves a `.cdk-overlay-backdrop` that survives both Escape and a
            # click on itself (measured), and it intercepts the upload button's click. This
            # is a MEASUREMENT lever only — production never attaches anything after the
            # avatar, because the combination is refused. Ripping the node out is the only
            # way to reach the question this spike exists to answer.
            # One sweep is not enough: the backdrop is re-created, and the NEXT click
            # (the composer) is blocked again. A MutationObserver keeps the page clear for
            # the rest of the run.
            await page.evaluate(
                """() => {
                    window.__killed = 0;
                    const kill = () => {
                      for (const n of document.querySelectorAll('.cdk-overlay-backdrop')) {
                        n.remove(); window.__killed++;
                      }
                    };
                    kill();
                    new MutationObserver(kill).observe(document.body,
                        {childList: true, subtree: true});
                }"""
            )
            await page.wait_for_timeout(500)
            report["backdrops_removed"] = await page.evaluate("() => window.__killed || 0")
            media_ids = await composer.attach_references(page, project_id, (ref,))
        report["uploaded_media_ids"] = list(media_ids)
        report["likeness_chips"] = await page.locator(LIKENESS_CHIP).count()
        report["mention_chips"] = await composer.read_chips(page)

        await composer.send_prompt(page, "a presenter holds the product", append=True)
        await page.evaluate(BLOCK_JS)

        submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
        report["submit_found"] = bool(await submit.count())
        if await submit.count():
            try:
                await asyncio.wait_for(submit.click(timeout=5000), timeout=25)
            except Exception as exc:  # noqa: BLE001 — the click may hang on a blocked fetch
                report["click_note"] = f"{type(exc).__name__}: {exc}"
            await page.wait_for_timeout(4000)
        captured = await page.evaluate("() => window.__captured || []")
        report["captured_count"] = len(captured)
        bodies = []
        for c in captured:
            raw = urllib.parse.unquote_plus(c.get("body") or "")
            ids = sorted(set(UUID.findall(raw)))
            bodies.append(
                {
                    "via": c.get("via"),
                    "rpcids": sorted(set(re.findall(r'"([A-Za-z0-9]{5,7})"', raw)))[:14],
                    "model_keys": sorted(set(MODEL_KEY.findall(raw)))[:6],
                    "uuid_count": len(ids),
                    "uploaded_ref_present": [m for m in media_ids if m.lower() in raw.lower()],
                    "ids": ids[:12],
                }
            )
        report["bodies"] = bodies
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    ap.add_argument("ref", type=Path)
    ap.add_argument(
        "--order",
        choices=("ref-first", "avatar-first", "avatar-only", "popover-both"),
        default="ref-first",
    )
    ap.add_argument("--model", default="omni_flash", help="empty string to leave as-is")
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.ref, args.order, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
