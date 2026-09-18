"""What does the Avatars tab render AFTER references are already on the prompt?

$0 — uploads one local file, attaches it as a mention (the real r2v sequence), then opens
the prompt-box popover, selects the Avatars tab by its `face` ligature and dumps the
overlay at several waits. Nothing is submitted.

A clean-composer spike found `flow-likeness-intro-view` here. The live r2v run, which
attaches references first, found neither that nor a tile — so either the state or the
timing differs, and the driver must not guess which.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    ADD_MENU_POPOVER,
    AVATAR_TAB,
    INGREDIENTS_LIGATURE,
    PROMPT_BOX_ADD,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

DUMP_JS = """
() => {
  const ov = document.querySelector('.cdk-overlay-container');
  if (!ov) return {note: 'no overlay container'};
  const names = {};
  for (const el of ov.querySelectorAll('*')) {
    const t = el.tagName.toLowerCase();
    if (t.startsWith('flow-')) names[t] = (names[t] || 0) + 1;
  }
  return {
    components: names,
    tabs: [...document.querySelectorAll("[role='tab']")].map(t => ({
      text: (t.textContent || '').trim().slice(0, 22),
      selected: t.getAttribute('aria-selected')})),
    text: (ov.textContent || '').trim().slice(0, 320),
    buttons: [...ov.querySelectorAll('button')].slice(0, 14).map(b => {
      const chain = [];
      for (let e = b; e && e !== ov; e = e.parentElement) {
        const t = e.tagName.toLowerCase();
        if (t.startsWith('flow-')) chain.push(t);
      }
      return {text: (b.textContent || '').trim().slice(0, 30),
              aria: b.getAttribute('aria-label'),
              cls: String(b.className).slice(0, 60),
              ligs: [...b.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim()),
              chain: chain.slice(0, 4)};
    }),
  };
}
"""


async def _main(profile: str, project_id: str, ref: Path) -> int:
    report: dict[str, Any] = {"project": project_id}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        await composer.ensure_editor(page, project_id)
        pane = await composer._open_pane(page)  # noqa: SLF001
        try:
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)  # noqa: SLF001
        finally:
            await composer._close_pane(page, strict=False)  # noqa: SLF001

        # The real sequence: a reference lands on the prompt BEFORE the avatar is asked for.
        await composer.attach_references(page, project_id, (ref,))
        report["chips_after_ref"] = await composer.read_chips(page)

        await page.locator(PROMPT_BOX_ADD).first.click(timeout=5000)
        await page.wait_for_timeout(1500)
        report["popover_opened"] = await page.evaluate(DUMP_JS)

        tab = page.locator(AVATAR_TAB).first
        report["tab_found"] = bool(await tab.count())
        if await tab.count():
            await tab.click(timeout=5000)
            await page.wait_for_timeout(2500)
            it = page.locator(f"{ADD_MENU_POPOVER} flow-add-menu-asset-item").first
            report["asset_items"] = await page.locator(
                f"{ADD_MENU_POPOVER} flow-add-menu-asset-item"
            ).count()
            if await it.count():
                await it.click(timeout=5000)
                await page.wait_for_timeout(1200)
            for wait_ms in (1500, 3000, 5000):
                await page.wait_for_timeout(wait_ms if wait_ms == 1500 else wait_ms - 1500)
                report[f"after_tab_{wait_ms}ms"] = await page.evaluate(DUMP_JS)
                report[f"tiles_{wait_ms}ms"] = await page.locator(
                    f"{ADD_MENU_POPOVER} flow-grid-tile-container"
                ).count()
        await page.keyboard.press("Escape")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    ap.add_argument("ref", type=Path)
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.ref))


if __name__ == "__main__":
    raise SystemExit(main())
