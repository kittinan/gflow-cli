"""What is inside the migrated composer's **Avatars** tab, and how is one attached?

$0 — opens the prompt-box attach dialog, selects the Avatars tab by its `face` ligature
(never by the translated label), and dumps what the tab renders plus what a tile click
puts on the prompt. Nothing is submitted.

Context: the Avatars tab exists. The first likeness spike missed it because it clicked the
PROJECT toolbar `add` (Upload / New collection / Create character / New scene) and searched
for a `face` ligature while this dialog was CLOSED. The tab only exists once it is open.
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
    INGREDIENTS_LIGATURE,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

PROMPT_BOX_ADD = "xpath=//flow-prompt-box//button[.//mat-icon[normalize-space()='add']]"
#: Tier-1: the tab carrying the `face` ligature. Its label is translated; the glyph is not.
AVATAR_TAB = "[role='tab']:has(mat-icon:text-is('face'))"

TILES_JS = """
() => {
  const ov = document.querySelector('.cdk-overlay-container');
  const panel = document.querySelector("[role='tabpanel']") || ov;
  if (!panel) return {panel_tag: null, note: 'no overlay container and no tabpanel'};
  const kids = [...panel.querySelectorAll('*')].filter(e => {
    const t = e.tagName.toLowerCase();
    return t.startsWith('flow-') || e.getAttribute('role') === 'option'
        || t === 'button' || t === 'img';
  });
  return {
    panel_tag: panel.tagName.toLowerCase(),
    panel_class: String(panel.className).slice(0, 90),
    tabs_now: [...document.querySelectorAll("[role='tab']")].map(t => ({
      text: (t.textContent || '').trim().slice(0, 24),
      selected: t.getAttribute('aria-selected')})),
    panel_text: (panel.textContent || '').trim().slice(0, 250),
    child_count: kids.length,
    children: kids.slice(0, 25).map(e => ({
      tag: e.tagName.toLowerCase(),
      aria: e.getAttribute('aria-label'),
      src: e.tagName.toLowerCase() === 'img' ? String(e.src).slice(0, 70) : null,
      text: (e.textContent || '').trim().slice(0, 50),
      ligs: [...e.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim()).slice(0, 4)}))
  };
}
"""

CHIPS_JS = """
() => [...document.querySelectorAll('[data-reference-type], [data-entity-id]')].map(c => ({
  refType: c.getAttribute('data-reference-type'),
  entityId: c.getAttribute('data-entity-id'),
  text: (c.textContent || '').trim().slice(0, 50)}))
"""


async def _main(profile: str, project_id: str, click_tile: bool) -> int:
    report: dict[str, Any] = {"project": project_id, "clicked_tile": click_tile}
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

        report["chips_before"] = await page.evaluate(CHIPS_JS)

        await page.locator(PROMPT_BOX_ADD).first.click(timeout=5000)
        await page.wait_for_timeout(1800)

        tab = page.locator(AVATAR_TAB).first
        report["avatar_tab_found"] = bool(await tab.count())
        if not await tab.count():
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2
        await tab.click(timeout=5000)
        await page.wait_for_timeout(1800)
        report["avatar_tab_contents"] = await page.evaluate(TILES_JS)

        if click_tile:
            # The first real tile in the panel, by structure: a grid tile container.
            tile = page.locator("[role='dialog'] flow-grid-tile-container").first
            report["tile_count"] = await tile.count()
            if await tile.count():
                await tile.click(timeout=5000)
                await page.wait_for_timeout(2000)
                report["after_tile_click_dialog_open"] = bool(
                    await page.locator("[role='dialog']").count()
                )
                report["after_tile_click_tiles"] = await page.evaluate(TILES_JS)
                report["chips_after"] = await page.evaluate(CHIPS_JS)

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        await page.keyboard.press("Escape")
        report["chips_final"] = await page.evaluate(CHIPS_JS)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    ap.add_argument("--click-tile", action="store_true", help="click the first avatar tile")
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.click_tile))


if __name__ == "__main__":
    raise SystemExit(main())
