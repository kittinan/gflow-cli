"""R2V on the migrated host: routing, upload, mention attach, and the submit rpc.

Deliberately NOT built on `test_migrated_composer.py`'s fake DOM. That fake models the
settings pane and the model menu; the reference path needs a mention picker, an upload
chooser and chips, and bolting those onto it would make one fake serve two very different
surfaces. These are small purpose-built doubles instead, each modelling exactly one
measured behaviour from
`docs/superpowers/spikes/2026-09-05-migrated-r2v-attach-surface.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports.migrated_composer import FRAME_SEARCH_ATTEMPTS
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import ReferenceNotFoundError, UiSelectorDriftError

pytestmark = pytest.mark.anyio


def _r2v(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {
        "prompt": "a woman holding the product",
        "mode": Mode.R2V,
        "aspect": Aspect.PORTRAIT,
    }
    base.update(kw)
    return GenerateVideoRequest(**base)


# --- routing ----------------------------------------------------------------


def test_migrated_can_serve_takes_r2v_from_local_files_only() -> None:
    from gflow_cli.api.transports.migrated_composer import _unported_form, migrated_can_serve

    assert migrated_can_serve(_r2v(reference_images=(Path("a.png"),)), "p1")
    # By NAME is refused for the same reason i2v refuses a UUID frame: the picker lists
    # assets by display name with no media id, so a reference gflow did not upload has
    # nothing to anchor on — and nothing to assert on the submit body.
    assert _unported_form(_r2v(ref_names=("product1.png",))) is not None
    assert not migrated_can_serve(_r2v(ref_names=("product1.png",)), "p1")
    # Character entities are a different attach surface (a chip with an entity_id, in a
    # different wire slot) and stay on labs.
    assert not migrated_can_serve(_r2v(reference_entities=("abc",)), "p1")
    # Creating a fresh project is not ported to that composer, so the gate keeps
    # requiring an existing one here.
    assert not migrated_can_serve(_r2v(reference_images=(Path("a.png"),)), None)


def test_a_submit_that_lost_the_references_is_a_wire_problem() -> None:
    """The expensive failure: the picker closes having inserted nothing, the app submits
    anyway, and Flow bills a clip with none of the user's references on it."""
    from gflow_cli.api.transports.migrated_composer import _r2v_body_problem

    good = '["veo_3_1_r2v_lite_low_priority", "aaaaaaaa-1111-2222-3333-444444444444"]'
    assert _r2v_body_problem(good, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",)) is None

    t2v_key = '["veo_3_1_lite_low_priority", "aaaaaaaa-1111-2222-3333-444444444444"]'
    problem = _r2v_body_problem(t2v_key, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",))
    assert problem is not None and "no reference was bound" in problem

    other_id = '["veo_3_1_r2v_lite_low_priority", "bbbbbbbb-1111-2222-3333-444444444444"]'
    problem = _r2v_body_problem(other_id, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",))
    assert problem is not None and "missing 1 of 1" in problem

    assert "could not be read" in (_r2v_body_problem("", "MZZa6b", ("x",)) or "")

    # The migrated host's t2v key carries no mode infix at all, and it is the key the
    # r2v diagnostic most needs to name: it is what the body says when the picker
    # inserted nothing. A mode-only MODEL_KEY reported "no model key" here.
    bare = '["veo_3_1_lite_lower_priority", "aaaaaaaa-1111-2222-3333-444444444444"]'
    problem = _r2v_body_problem(bare, "MZZa6b", ("aaaaaaaa-1111-2222-3333-444444444444",))
    assert problem is not None and "veo_3_1_lite_lower_priority" in problem


def test_a_t2v_key_carrying_a_duration_names_the_duration_as_the_cause() -> None:
    """The measured cause of a degraded r2v submit, not a guess: below 8s this host
    flattens the mentions and goes out as t2v. "No reference was bound" on its own sent
    the first reporter reading the attach code, which was working correctly."""
    from gflow_cli.api.transports.migrated_composer import _r2v_body_problem

    ref = "aaaaaaaa-1111-2222-3333-444444444444"
    degraded = f'["veo_3_1_t2v_lite_4s_low_priority", "{ref}"]'
    problem = _r2v_body_problem(degraded, "MZZa6b", (ref,))
    assert problem is not None
    assert "veo_3_1_t2v_lite_4s_low_priority" in problem
    assert "only at 8s" in problem and "--duration" in problem

    # A t2v key with no duration segment is a different fault and must not blame duration.
    plain = f'["veo_3_1_lite_lower_priority", "{ref}"]'
    other = _r2v_body_problem(plain, "MZZa6b", (ref,))
    assert other is not None and "only at 8s" not in other


def test_the_submit_body_is_form_decoded_before_it_is_quoted_back() -> None:
    """`batchexecute` sends `f.req=<percent-encoded JSON>`. Raw, the key reads as
    `%5C%22veo_...%5C%22` and a real run quoted it back to the user as
    `22veo_3_1_t2v_lite_4s_low_priority`."""
    from gflow_cli.api.transports.migrated_composer import _post_data

    class _Req:
        post_data = (
            "f.req=%5B%5B%5B%22MZZa6b%22%2C%22%5B%5C%22veo_3_1_r2v_lite%5C%22%5D%22%5D%5D%5D"
        )

    body = _post_data(_Req())
    # The nested JSON keeps its own backslash escapes; what must be gone is the percent
    # encoding, whose `%5C%22` is what MODEL_KEY was matching as a leading "22".
    assert "%5C%22" not in body and "%22" not in body
    assert "veo_3_1_r2v_lite" in body and body.startswith('f.req=[[["MZZa6b"')

    class _Boom:
        @property
        def post_data(self) -> str:
            raise RuntimeError("undecodable bytes")

    assert _post_data(_Boom()) == ""  # a listener must never raise


def test_submit_rpcs_cover_the_ingredients_submit() -> None:
    """An Ingredients run submits on MZZa6b, not YhhmEf. Watching only the latter is why
    every early capture reported "no submit" while the request was plainly being made."""
    from gflow_cli.api.transports.migrated_composer import SUBMIT_RPCS

    assert "YhhmEf" in SUBMIT_RPCS  # t2v
    assert "MZZa6b" in SUBMIT_RPCS  # r2v / ingredients


# --- a tiny composer double -------------------------------------------------


class FakeLoc:
    def __init__(self, page: FakeComposerPage, kind: str, items: list[Any]) -> None:
        self.page, self.kind, self.items = page, kind, items

    @property
    def first(self) -> FakeLoc:
        return FakeLoc(self.page, self.kind, self.items[:1])

    def filter(self, *, has: Any = None, has_text: Any = None) -> FakeLoc:
        if has_text == "Upload media":
            return FakeLoc(self.page, "upload", ["upload"] if self.page.upload_button else [])
        return self

    async def count(self) -> int:
        return len(self.items)

    async def all_text_contents(self) -> list[str]:
        return [str(i) for i in self.items]

    async def focus(self) -> None:
        return None

    async def click(self, **_: Any) -> None:
        if self.kind == "upload":
            self.page.upload_clicked += 1
        if self.kind == "composer":
            self.page.composer_clicks += 1


class FakeKeyboard:
    def __init__(self, page: FakeComposerPage) -> None:
        self.page = page

    async def type(self, text: str, **_: Any) -> None:
        self.page.typed.append(text)

    async def press(self, key: str) -> None:
        self.page.typed.append(f"<{key}>")
        if key == "Enter":
            self.page.on_enter()

    async def insert_text(self, text: str) -> None:
        self.page.typed.append(text)


class FakeComposerPage:
    """Only what the reference path touches: a composer, an asset list, chips."""

    def __init__(
        self,
        *,
        assets: list[str] | None = None,
        chips_per_enter: int = 1,
        upload_button: bool = True,
        miss_first: int = 0,
    ) -> None:
        self.keyboard = FakeKeyboard(self)
        self.typed: list[str] = []
        self.chips: list[dict[str, str]] = []
        self.assets = assets if assets is not None else ["me.jpgImage"]
        self.chips_per_enter = chips_per_enter
        self.upload_button = upload_button
        #: How many leading ENTERs insert nothing — the search index not yet holding a
        #: fresh upload, which `_pick_frame_by_name` already retries through.
        self.miss_first = miss_first
        self.enters = 0
        self.upload_clicked = 0
        self.composer_clicks = 0

    def on_enter(self) -> None:
        self.enters += 1
        if self.enters <= self.miss_first:
            return
        for _ in range(self.chips_per_enter):
            self.chips.append(
                {"text": f"asset{len(self.chips)}", "entity_id": "", "reference_type": "media"}
            )

    def locator(self, css: str) -> FakeLoc:
        if css == "[contenteditable='true']":
            return FakeLoc(self, "composer", ["composer"])
        if css == "button.asset-item[role='option']":
            return FakeLoc(self, "asset", list(self.assets))
        if css == "button":
            return FakeLoc(self, "button", ["button"])
        if css == ".cdk-overlay-backdrop":
            return FakeLoc(self, "backdrop", [])
        raise AssertionError(f"unmodelled selector: {css!r}")

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, _script: str, _arg: Any = None) -> Any:
        return self.chips


# --- attach -----------------------------------------------------------------


async def test_each_reference_must_land_as_its_own_chip() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage()
    composer = MigratedComposer()
    await composer._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    await composer._mention_by_name(page, "product1.png", expect_chips=2)  # noqa: SLF001
    # ENTER is what commits a mention — a typed query alone inserts nothing.
    assert page.typed.count("<Enter>") == 2
    assert len(page.chips) == 2


async def test_a_picker_that_misses_the_first_query_is_retried() -> None:
    """Same measured failure `_pick_frame_by_name` retries through: the asset search is
    server-side and does not always index a fresh upload by the first query. One query
    behind a fixed wait would fail a reference that is merely late."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage(miss_first=1)
    await MigratedComposer()._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    assert page.enters == 2 and len(page.chips) == 1
    # The failed query is backspaced out before re-querying, or the composer keeps a
    # literal "@me.jpg" alongside the chip that eventually lands.
    assert page.typed.count("<Backspace>") == len("me.jpg") + 1


async def test_a_retry_that_would_eat_an_attached_chip_refuses_instead() -> None:
    """Backspacing the failed query is a fixed keystroke count, and a chip is deleted by
    one Backspace. If the clean-up takes a reference someone already attached, the prompt
    is no longer the one that was built — abandon rather than submit a silently different
    generation."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage(miss_first=99)
    page.chips = [{"text": "first", "entity_id": "", "reference_type": "media"}]

    async def eat_a_chip(_script: str, _arg: Any = None) -> Any:
        if page.typed.count("<Backspace>"):
            page.chips = []
        return page.chips

    page.evaluate = eat_a_chip  # type: ignore[method-assign]
    with pytest.raises(ReferenceNotFoundError, match="already-attached reference"):
        await MigratedComposer()._mention_by_name(page, "second.png", expect_chips=2)  # noqa: SLF001


async def test_a_reference_that_does_not_attach_is_refused_before_submit() -> None:
    """The picker silently inserting nothing is the failure mode that would otherwise
    generate — and bill — a clip with none of the user's references on it."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage(assets=["somethingelseImage"], chips_per_enter=0)
    with pytest.raises(ReferenceNotFoundError) as exc_info:
        await MigratedComposer()._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    message = str(exc_info.value)
    assert "me.jpg" in message
    assert "somethingelse" in message  # says what the picker DID offer
    assert page.enters == FRAME_SEARCH_ATTEMPTS  # every attempt spent before giving up


async def test_the_prompt_is_appended_so_the_mentions_survive() -> None:
    """Clicking the composer would move the caret away from the last mention; an r2v run
    must add its text after the chips, not on top of them."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage()
    await MigratedComposer().send_prompt(page, "a woman holding it", append=True)
    assert page.composer_clicks == 0
    assert "a woman holding it" in page.typed


# --- character entities (#723) ----------------------------------------------


class _PickerOption:
    def __init__(self, title: str, kind: str, entity_id: str = "") -> None:
        self.title, self.kind, self.entity_id = title, kind, entity_id


class _CharLoc:
    def __init__(self, page: FakeCharacterPickerPage, kind: str, items: list[Any]) -> None:
        self.page, self.kind, self.items = page, kind, items

    @property
    def first(self) -> _CharLoc:
        return _CharLoc(self.page, self.kind, self.items[:1])

    def nth(self, i: int) -> _CharLoc:
        return _CharLoc(self.page, self.kind, self.items[i : i + 1])

    async def count(self) -> int:
        return len(self.items)

    async def all_text_contents(self) -> list[str]:
        return [str(i) for i in self.items]

    async def focus(self) -> None:
        return None

    async def wait_for(self, **_: Any) -> None:
        if self.kind == "tab" and self.page.tab_missing_first > 0:
            self.page.tab_missing_first -= 1
            raise TimeoutError("tab not rendered yet")
        if not self.items:
            raise TimeoutError("never rendered")

    async def fill(self, text: str) -> None:
        self.page.searches += 1
        self.page.query = text

    async def click(self, **_: Any) -> None:
        if self.kind == "tab":
            self.page.tab_clicks += 1
            self.page.tab_selected = True
        elif self.kind == "option":
            opt: _PickerOption = self.items[0]
            self.page.chips.append(
                {"text": opt.title, "entity_id": opt.entity_id, "reference_type": opt.kind}
            )


class FakeCharacterPickerPage:
    """The migrated ``@`` picker as measured live on 2026-09-18.

    One popover lists media and characters together, unranked: ``@tun`` offered fourteen
    ``tun_portrait-*.jpg`` files BEFORE the character ``Tun``, so Enter (which commits the
    first option) always took a file. The category rail's Characters tab narrows the list
    to characters; clicking an option commits it as a chip, and Escape on a miss removes
    the ``@`` by itself.
    """

    def __init__(
        self,
        library: list[_PickerOption],
        *,
        has_tab: bool = True,
        tab_filters: bool = True,
        miss_first: int = 0,
        tab_missing_first: int = 0,
    ) -> None:
        self.keyboard = FakeKeyboard(self)  # type: ignore[arg-type]
        self.library = library
        self.has_tab, self.tab_filters, self.miss_first = has_tab, tab_filters, miss_first
        #: Live 2026-09-18: in a full CLI run the picker rendered its rail slower than
        #: the fixed 2.2 s wait, so the tab was "missing" on a picker that was opening.
        self.tab_missing_first = tab_missing_first
        self.typed: list[str] = []
        self.chips: list[dict[str, str]] = []
        self.tab_selected = False
        self.query = ""
        self.searches = 0
        self.tab_clicks = 0

    def on_enter(self) -> None:
        raise AssertionError("Enter commits the FIRST option — it must not be used here")

    def _options(self) -> list[_PickerOption]:
        if self.searches <= self.miss_first:
            return []
        found = [o for o in self.library if self.query.casefold() in o.title.casefold()]
        if self.tab_selected and self.tab_filters:
            found = [o for o in found if o.kind == "entity"]
        return found

    def locator(self, css: str) -> _CharLoc:
        from gflow_cli.api.transports import migrated_composer as mc

        if css == mc.COMPOSER:
            return _CharLoc(self, "composer", ["composer"])
        if css == mc.PICKER_CHARACTERS_TAB:
            return _CharLoc(self, "tab", ["tab"] if self.has_tab else [])
        if css == f"{mc.PICKER} {mc.PICKER_SEARCH}":
            return _CharLoc(self, "search", ["search"])
        if css == f"{mc.PICKER_OPTION} {mc.PICKER_OPTION_TITLE}":
            return _CharLoc(self, "title", [o.title for o in self._options()])
        if css == mc.PICKER_OPTION:
            return _CharLoc(self, "option", self._options())
        if css == mc.PICKER_CONFIRM:
            return _CharLoc(self, "confirm", [])
        if css == mc.PICKER:
            return _CharLoc(self, "popover", ["popover"])
        raise AssertionError(f"unmodelled selector: {css!r}")

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def evaluate(self, _script: str, _arg: Any = None) -> Any:
        return self.chips


def _tun_project() -> list[_PickerOption]:
    """The live 2026-09-18 listing for ``tun``: files first, the character last."""
    files = [_PickerOption(f"tun_portrait-{i:08x}.jpg", "media") for i in range(14)]
    return [*files, _PickerOption("Tun", "entity", "42a8618b")]


async def test_character_is_picked_even_when_files_sharing_its_name_are_listed_first() -> None:
    """The live failure: Enter committed ``tun_portrait-bbd47565.jpg`` instead of Tun."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(_tun_project())
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("42a8618b",),
        names=("tun",),
    )
    assert page.chips == [{"text": "Tun", "entity_id": "42a8618b", "reference_type": "entity"}]
    assert page.tab_clicks == 1


async def test_character_mentions_land_as_entity_chips() -> None:
    """The whole point: the chip must carry the ENTITY id, not merely exist."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(
        [_PickerOption("Kael", "entity", "ent-kael"), _PickerOption("Naia", "entity", "ent-naia")]
    )
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("ent-kael", "ent-naia"),
        names=("Kael", "Naia"),
    )
    assert [c["entity_id"] for c in page.chips] == ["ent-kael", "ent-naia"]
    assert {c["reference_type"] for c in page.chips} == {"entity"}


async def test_the_exact_name_wins_over_a_character_that_merely_contains_it() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(
        [_PickerOption("Tun Junior", "entity", "ent-jr"), _PickerOption("Tun", "entity", "ent-tun")]
    )
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("ent-tun",),
        names=("Tun",),
    )
    assert [c["entity_id"] for c in page.chips] == ["ent-tun"]


async def test_a_late_index_is_retried_with_escape_never_backspace() -> None:
    """Escape on a miss drops the ``@`` by itself (measured); a Backspace could eat a chip."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(_tun_project(), miss_first=1)
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("42a8618b",),
        names=("tun",),
    )
    assert page.searches == 2 and len(page.chips) == 1
    after_first_at = page.typed[page.typed.index("@") :]
    assert "<Escape>" in after_first_at
    assert "<Backspace>" not in after_first_at  # the up-front clear_composer is separate


async def test_a_slow_picker_is_waited_for_and_retried_not_called_drift() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(_tun_project(), tab_missing_first=1)
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("42a8618b",),
        names=("tun",),
    )
    assert [c["reference_type"] for c in page.chips] == ["entity"]
    assert page.typed.count("@") == 2
    assert "<Escape>" in page.typed


async def test_no_characters_tab_is_selector_drift_not_a_guess() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(_tun_project(), has_tab=False)
    with pytest.raises(UiSelectorDriftError, match="accessibility_new"):
        await MigratedComposer().attach_character_entities(
            page,  # type: ignore[arg-type]
            entity_ids=("42a8618b",),
            names=("tun",),
        )
    assert page.chips == []


async def test_a_media_chip_where_a_character_was_asked_for_is_refused() -> None:
    """Belt and braces: if the tab ever stops filtering, the chip read-back still refuses.

    Measured 2026-09-07: the same ``@Kael`` query committed an entity on one gesture and
    a JPEG sharing the name on another. A media chip where a character was asked for is
    a clip that looks right and drifts on the next cut, so it never reaches submit.
    """
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage([_PickerOption("Kael", "media")], tab_filters=False)
    with pytest.raises(ReferenceNotFoundError, match="media"):
        await MigratedComposer().attach_character_entities(
            page,  # type: ignore[arg-type]
            entity_ids=("ent-kael",),
            names=("Kael",),
        )


async def test_the_wrong_entity_is_refused_even_though_a_chip_landed() -> None:
    """A chip of the right KIND is not proof it is the right PERSON."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage([_PickerOption("Kael", "entity", "ent-someone-else")])
    with pytest.raises(ReferenceNotFoundError, match="ent-kael"):
        await MigratedComposer().attach_character_entities(
            page,  # type: ignore[arg-type]
            entity_ids=("ent-kael",),
            names=("Kael",),
        )


def test_a_named_character_reference_is_served_now_that_a_submit_completes() -> None:
    """The gate this replaces demanded exactly one thing to lift it: a completed run.

    It refused every entity-bound request because the submit was believed to answer with
    a null payload — `parse_frames` keeps only frames whose payload slot is a string, so
    such a reply yields nothing, `submitted` never resolves, and the run exits 9 after
    60 s while Flow generates anyway. That was measured once, on one account, in 2026-09.

    It does not reproduce. A live r2v carrying `--reference-entity` plus a local `--ref`
    (`veo-lite-lp`, 2026-09-12) logged `submit_reply_shape rpc=MZZa6b frames=1
    submit_frames=1 body_bytes=6766` — a real payload naming the media id at status 6 —
    then `jwpduf` 2 -> 3, `as29s`, and an 8 s 720x1280 clip on disk. Exit 0.

    So the request is served, and the only thing still refused is the one the picker
    genuinely cannot do: Flow's search offers no id to anchor on, so an entity with no
    display name has nothing to type and would submit without it.
    """
    from gflow_cli.api.transports.migrated_composer import _unported_form

    assert _unported_form(_r2v(reference_entities=("ent-kael",))) is not None
    assert (
        _unported_form(_r2v(reference_entities=("ent-kael",), reference_entity_names=("Kael",)))
        is None
    )


def test_the_avatar_rides_with_references_only_on_omni_flash() -> None:
    """Whether a likeness may share a prompt with a reference is MODEL state.

    This shipped first as a blanket refusal, on two $0 body captures that agreed the
    uploaded media id was absent. Both were taken on whatever veo tier the editor
    remembered, because the spike set mode and sub-mode and never selected a model — so
    they measured the tier, not the feature. The account owner said plainly that it works
    in Flow's own UI, which it does. Re-measured with the model selected first:

        omni-flash -> abra_r2v_10s, and the uploaded media id IS in the body
        veo tiers  -> the upload is absent

    Live on omni-flash: `--ref ... --avatar --duration 10` returned exit 0 and a 10.006 s
    720x1280 clip. The duration row is model state in the same way (10s only there), so
    this is the rule on this host rather than a special case.

    `--model` is REQUIRED for the combination: with `model=None` the editor submits on
    whatever tier it last used, which is precisely the silent drop being guarded against.
    """
    from gflow_cli.api.transports.migrated_composer import _unported_form

    ref = (Path("a.png"),)
    assert (
        _unported_form(_r2v(reference_images=ref, use_avatar=True, model=VideoModel.OMNI_FLASH))
        is None
    )

    # A veo tier drops the reference, so it is refused — and the message names the model.
    assert (
        _unported_form(_r2v(reference_images=ref, use_avatar=True, model=VideoModel.VEO_3_1_LITE))
        == "the Avatar together with references on veo_3_1_lite"
    )

    # No explicit model is refused too: the editor would pick the remembered tier.
    assert _unported_form(_r2v(reference_images=ref, use_avatar=True)) == (
        "the Avatar together with references on no explicit --model"
    )

    # The avatar ALONE stays served on any tier — nothing to drop.
    avatar_only = GenerateVideoRequest(prompt="walking", mode=Mode.AVATAR, aspect=Aspect.PORTRAIT)
    assert _unported_form(avatar_only) is None


def test_a_character_run_is_moved_onto_the_migrated_host_like_any_other() -> None:
    """`migrated_can_serve` kept its own entity refusal after the port landed.

    It only decides whether to pull an *unmoved* account onto the new host, so it never
    fired for the accounts that needed it — an account Flow has already moved is routed
    by its URL and never asks. Left in place it would send an unmoved account to the labs
    driver for a request the migrated composer now serves.
    """
    from gflow_cli.api.transports.migrated_composer import migrated_can_serve

    req = _r2v(reference_entities=("ent-kael",), reference_entity_names=("Kael",))
    assert migrated_can_serve(req, "proj-1") is True
    # The name requirement still travels with it: no name, no picker query, no move.
    assert migrated_can_serve(_r2v(reference_entities=("ent-kael",)), "proj-1") is False


async def test_a_character_after_a_reference_chip_types_at_the_end_of_the_prompt() -> None:
    """Live 2026-09-18 (r2v --ref + --reference-entity): the reference chip sat where the
    composer click landed, the ``@`` went nowhere, and no picker opened — reported as a
    missing Characters tab. The caret is now sent to the end before every ``@``."""
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeCharacterPickerPage(_tun_project())
    page.chips = [{"text": "ref.png", "entity_id": "", "reference_type": "media"}]
    await MigratedComposer().attach_character_entities(
        page,  # type: ignore[arg-type]
        entity_ids=("42a8618b",),
        names=("tun",),
        clear=False,
    )
    at = page.typed.index("@")
    assert page.typed[at - 1] == "<Control+End>"
    assert [c["reference_type"] for c in page.chips] == ["media", "entity"]


async def test_a_second_media_reference_also_types_at_the_end() -> None:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

    page = FakeComposerPage()
    composer = MigratedComposer()
    await composer._mention_by_name(page, "me.jpg", expect_chips=1)  # noqa: SLF001
    await composer._mention_by_name(page, "product1.png", expect_chips=2)  # noqa: SLF001
    ats = [i for i, k in enumerate(page.typed) if k == "@"]
    assert all(page.typed[i - 1] == "<Control+End>" for i in ats)
