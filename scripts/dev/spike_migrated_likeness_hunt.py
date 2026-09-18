"""Is the Flow Avatar (likeness) reachable ANYWHERE on the migrated composer?

$0 — opens the editor, opens every attach surface it can find, and dumps what each
one offers. Nothing is typed into a generation and nothing is submitted.

The first pass at this question (spike_migrated_avatar_and_duration.py) clicked the
PROJECT toolbar `add` and found Upload / New collection / Create character / New scene.
That button is defined as `[not(ancestor::flow-prompt-box)]` — i.e. the driver already
knows a SECOND `add` exists inside the prompt box, and that is the per-generation attach.
On labs.google the likeness lives exactly there: Add Media -> a tab whose id ends
`-trigger-LIKENESS` -> include. A miss on the project toolbar says nothing about it.
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
    COMPOSER,
    INGREDIENTS_LIGATURE,
    MigratedComposer,
)

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

PROMPT_BOX_ADD = "xpath=//flow-prompt-box//button[.//mat-icon[normalize-space()='add']]"

#: Anything whose id/class/attribute names the concept, on any element.
MARKER_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const bag = [el.id, el.className, el.getAttribute('aria-label'),
                 el.getAttribute('data-reference-type'), el.getAttribute('role')]
                .filter(v => typeof v === 'string').join(' ').toLowerCase();
    if (/likeness|avatar|selfie|my ?face/.test(bag)) {
      out.push({tag: el.tagName.toLowerCase(), id: el.id,
                cls: String(el.className).slice(0, 80),
                aria: el.getAttribute('aria-label'),
                text: (el.textContent || '').trim().slice(0, 60)});
    }
  }
  return out.slice(0, 40);
}
"""

TABS_JS = """
() => [...document.querySelectorAll("[role='tab']")].map(t => ({
  id: t.id, aria: t.getAttribute('aria-label'),
  text: (t.textContent || '').trim().slice(0, 40),
  selected: t.getAttribute('aria-selected')}))
"""


async def _dump_options(page: Any, limit: int = 30) -> list[dict[str, Any]]:
    return await page.evaluate(
        """(limit) => [...document.querySelectorAll(
             "button.asset-item[role='option'], [role='option'], [role='menuitem']")]
           .slice(0, limit).map(o => ({
              text: (o.textContent || '').trim().slice(0, 60),
              refType: o.getAttribute('data-reference-type'),
              entityId: o.getAttribute('data-entity-id'),
              ligs: [...o.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim())}))""",
        limit,
    )


async def _main(profile: str, project_id: str) -> int:
    report: dict[str, Any] = {"project": project_id}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        await composer.ensure_editor(page, project_id)

        # The likeness lives behind the Ingredients sub-mode on labs; match that.
        pane = await composer._open_pane(page)  # noqa: SLF001
        try:
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)  # noqa: SLF001
        finally:
            await composer._close_pane(page, strict=False)  # noqa: SLF001

        report["markers_on_editor"] = await page.evaluate(MARKER_JS)
        report["tabs_on_editor"] = await page.evaluate(TABS_JS)

        # 1. The prompt-box add — the per-generation attach the other spike excluded.
        box_add = page.locator(PROMPT_BOX_ADD).first
        report["prompt_box_add_present"] = bool(await box_add.count())
        if await box_add.count():
            await box_add.click(timeout=5000)
            await page.wait_for_timeout(1800)
            report["prompt_box_add_options"] = await _dump_options(page)
            report["prompt_box_add_tabs"] = await page.evaluate(TABS_JS)
            report["prompt_box_add_markers"] = await page.evaluate(MARKER_JS)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        # 2. The `@` mention picker — what does it offer with no query at all?
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.type("@")
        await page.wait_for_timeout(2000)
        report["at_picker_options"] = await _dump_options(page, limit=40)
        report["at_picker_markers"] = await page.evaluate(MARKER_JS)
        await page.keyboard.press("Escape")
        await page.keyboard.press("Backspace")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id))


if __name__ == "__main__":
    raise SystemExit(main())
