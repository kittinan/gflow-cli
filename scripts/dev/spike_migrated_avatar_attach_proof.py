"""What does attaching the migrated Avatar actually CHANGE, if not a mention chip?

$0 — attaches one reference, opens the Avatars tab, clicks the avatar, and diffs the
prompt box before and after. Nothing is submitted.

The likeness is a separate wire slot on labs (`referenceLikenesses`), not a mention, so a
chip-count check is the wrong proof. This finds the real one.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gflow_cli.api.transports.migrated_composer import (  # noqa: E402
    ADD_MENU_ASSET_ITEM,
    ADD_MENU_POPOVER,
    AVATAR_TAB,
    INGREDIENTS_LIGATURE,
    PROMPT_BOX_ADD,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

BOX_JS = """
() => {
  const box = document.querySelector('flow-prompt-box');
  if (!box) return {note: 'no flow-prompt-box'};
  const comps = {};
  for (const el of box.querySelectorAll('*')) {
    const t = el.tagName.toLowerCase();
    if (t.startsWith('flow-') || t === 'img') comps[t] = (comps[t] || 0) + 1;
  }
  return {
    components: comps,
    text: (box.textContent || '').trim().slice(0, 300),
    imgs: [...box.querySelectorAll('img')].map(i => String(i.src).slice(0, 90)),
    attrs: [...box.querySelectorAll('[data-reference-type],[data-entity-id],[data-likeness-id]')]
      .map(e => ({rt: e.getAttribute('data-reference-type'),
                  eid: e.getAttribute('data-entity-id'),
                  lid: e.getAttribute('data-likeness-id'),
                  text: (e.textContent || '').trim().slice(0, 40)})),
    likeness: [...box.querySelectorAll('flow-likeness-ingredient-chip')].map(c => ({
      text: (c.textContent || '').trim().slice(0, 80),
      cls: String(c.className).slice(0, 80),
      title: c.getAttribute('title'),
      ligs: [...c.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim()),
      html: c.innerHTML.slice(0, 400)})),
    errors: [...box.querySelectorAll('*')].filter(e =>
      [...e.querySelectorAll(':scope > i, :scope > mat-icon')].some(
        n => n.textContent.trim() === 'error')).slice(0, 4).map(e => ({
      tag: e.tagName.toLowerCase(), text: (e.textContent || '').trim().slice(0, 90),
      aria: e.getAttribute('aria-label'), title: e.getAttribute('title')})),
  };
}
"""


async def _main(profile: str, project_id: str, ref: Path) -> int:
    report: dict[str, Any] = {}
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
        await composer.attach_references(page, project_id, (ref,))

        before = await page.evaluate(BOX_JS)
        report["before"] = before

        await page.locator(PROMPT_BOX_ADD).first.click(timeout=5000)
        await page.wait_for_timeout(1500)
        await page.locator(AVATAR_TAB).first.click(timeout=5000)
        await page.wait_for_timeout(3000)
        items = page.locator(f"{ADD_MENU_POPOVER} {ADD_MENU_ASSET_ITEM}")
        report["items"] = await items.count()
        if await items.count():
            await items.first.click(timeout=5000)
            await page.wait_for_timeout(2500)
        report["popover_still_open"] = bool(await page.locator(ADD_MENU_POPOVER).count())
        after = await page.evaluate(BOX_JS)
        report["after"] = after

        b, a = before.get("components", {}), after.get("components", {})
        report["component_delta"] = {
            k: (b.get(k, 0), a.get(k, 0)) for k in set(b) | set(a) if b.get(k) != a.get(k)
        }
        report["text_diff"] = [
            ln
            for ln in difflib.unified_diff(
                [before.get("text", "")], [after.get("text", "")], lineterm=""
            )
        ][:6]
        report["new_imgs"] = [i for i in after.get("imgs", []) if i not in before.get("imgs", [])]

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
