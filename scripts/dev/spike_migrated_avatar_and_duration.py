"""Does the migrated composer expose an Avatar, and what durations does r2v offer?

$0 — opens the editor, opens the settings pane, reads radios and the Add menu.
Nothing is typed and nothing is submitted.

Two questions, both blocking work on the #639 matrix:

1. **Avatar/likeness.** `migrated_composer` has no likeness attach at all, so a
   `--avatar` request used to pass every gate and bill a clip without the presenter.
   Is there a control to bind to on this host, or is the refusal permanent?
2. **r2v duration.** The driver refuses any r2v `--duration` other than
   `R2V_DURATION_S` (8), on a measurement that 4s/6s drop the references. But real
   `abra_r2v_10s` records exist in a live project, so 10s r2v happens somehow.
   Enumerate the duration row per model instead of guessing.
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
    RADIO,
    RADIOGROUP,
    MigratedComposer,
)
from gflow_cli.api.video import VideoModel  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

#: Anything that could plausibly carry a likeness affordance, by STRUCTURE.
#: Text labels are deliberately absent — locale-invariance discipline.
AVATAR_LIGATURES = ("person", "face", "account_circle", "portrait", "self_improvement", "mood")


async def _dump_radios(pane: Any, label: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups = pane.locator(RADIOGROUP)
    for gi in range(await groups.count()):
        g = groups.nth(gi)
        radios = g.locator(RADIO)
        rows: list[dict[str, Any]] = []
        for ri in range(await radios.count()):
            r = radios.nth(ri)
            rows.append(
                {
                    "text": (await r.inner_text()).strip()[:40],
                    "aria_label": await r.get_attribute("aria-label"),
                    "checked": await r.get_attribute("aria-checked"),
                    "ligatures": await r.evaluate(
                        "el => [...el.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim())"
                    ),
                }
            )
        out.append({"group": gi, "where": label, "radios": rows})
    return out


async def _main(profile: str, project_id: str, model_name: str) -> int:
    report: dict[str, Any] = {"project": project_id, "model": model_name}
    composer = MigratedComposer()
    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 — dev instrument
        assert page is not None
        await composer.ensure_editor(page, project_id)

        pane = await composer._open_pane(page)  # noqa: SLF001
        try:
            await composer._select(page, pane, axis="mode", lig="videocam")  # noqa: SLF001
            await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)  # noqa: SLF001
            report["radios_before_model"] = await _dump_radios(pane, "ingredients")
            model = VideoModel(model_name)
            await composer._select_model(page, pane, model)  # noqa: SLF001
            report["radios_after_model"] = await _dump_radios(pane, f"ingredients+{model_name}")
        finally:
            await composer._close_pane(page, strict=False)  # noqa: SLF001

        # Question 1: any likeness affordance anywhere on the editor surface?
        found: list[dict[str, Any]] = []
        for lig in AVATAR_LIGATURES:
            loc = page.locator(f"i.google-symbols:text-is('{lig}'), mat-icon:text-is('{lig}')")
            n = await loc.count()
            if n:
                found.append({"ligature": lig, "count": n})
        report["avatar_ligatures_on_editor"] = found

        # The Add menu is where labs.google puts Add Media -> Avatar.
        add = page.locator(
            "xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]"
        ).first
        report["toolbar_add_present"] = bool(await add.count())
        if await add.count():
            await add.click(timeout=5000)
            await page.wait_for_timeout(1500)
            items = page.locator("[role='menuitem'], [role='option'], [role='dialog'] button")
            entries: list[dict[str, Any]] = []
            for i in range(min(await items.count(), 40)):
                it = items.nth(i)
                entries.append(
                    {
                        "text": (await it.inner_text()).strip()[:50],
                        "ligatures": await it.evaluate(
                            "el => [...el.querySelectorAll('i,mat-icon')].map(n => n.textContent.trim())"
                        ),
                    }
                )
            report["add_menu_entries"] = entries
            await page.keyboard.press("Escape")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("project_id")
    ap.add_argument("--model", default="omni_flash")
    args = ap.parse_args()
    return asyncio.run(_main(args.profile, args.project_id, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
