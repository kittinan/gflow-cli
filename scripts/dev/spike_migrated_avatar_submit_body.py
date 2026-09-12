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
    INGREDIENTS_LIGATURE,
    LIKENESS_CHIP,
    MigratedComposer,
    _ligature,
)

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


async def _main(profile: str, project_id: str, ref: Path, order: str) -> int:
    report: dict[str, Any] = {"order": order}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        await composer.ensure_editor(page, project_id)
        pane = await composer._open_pane(page)  # noqa: SLF001
        try:
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)  # noqa: SLF001
        finally:
            await composer._close_pane(page, strict=False)  # noqa: SLF001

        media_ids: tuple[str, ...] = ()
        if order == "avatar-only":
            await composer.attach_avatar(page)
        elif order == "ref-first":
            media_ids = await composer.attach_references(page, project_id, (ref,))
            await composer.attach_avatar(page)
        else:
            await composer.attach_avatar(page)
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
        "--order", choices=("ref-first", "avatar-first", "avatar-only"), default="ref-first"
    )
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.ref, args.order))


if __name__ == "__main__":
    raise SystemExit(main())
