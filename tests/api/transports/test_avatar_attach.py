"""Transport-level contract for the Avatar/likeness attach.

Two layers are exercised:

* ``_attach_likeness`` on its own, against a fake page whose set of "present"
  selectors is the input variable — so each test states exactly which DOM it is
  describing, and the assertions are about the resulting ACTION SEQUENCE and the
  error type, never about "a mock was called".
* the ``generate_video`` / ``generate_images`` orchestration, where the
  falsifiable claims are *which* sub-mode was entered, *which* attach helpers ran
  (and which deliberately did not), *in what order*, and whether a prompt was
  ever submitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    ADD_MEDIA_BUTTON,
    AVATAR_TAB_SELECTORS,
    DIALOG_ANY,
    PICKER_ANY_TAB,
    PICKER_INCLUDE_BUTTON,
    VideoGenerationMixin,
)
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel, VideoStatus
from gflow_cli.errors import (
    AvatarUnavailableError,
    TransportTimeoutError,
    UiSelectorDriftError,
)

_T2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
_I2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartImage"


# ---------------------------------------------------------------------------
# Fake page — the DOM is data, so each test names the DOM it describes
# ---------------------------------------------------------------------------


class _FakeTimeoutError(Exception):
    """Stand-in for playwright's TimeoutError (any exception type is caught)."""


class _FakeLocator:
    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    @property
    def last(self) -> _FakeLocator:
        return self

    async def wait_for(self, *, state: str = "visible", timeout: float = 0) -> None:
        del timeout
        if state == "visible":
            if self._selector not in self._page.present:
                raise _FakeTimeoutError(self._selector)
            return
        if state == "hidden":
            if self._selector == DIALOG_ANY and not self._page.dialog_closes:
                raise _FakeTimeoutError("dialog still open")
            return

    async def click(self, **kwargs: Any) -> None:
        del kwargs
        self._page.clicks.append(self._selector)

    async def count(self) -> int:
        return self._page.counts.get(self._selector, 0)


class _FakePage:
    """A page defined by which selectors are visible and whether the picker closes."""

    def __init__(
        self,
        *,
        present: set[str],
        counts: dict[str, int] | None = None,
        dialog_closes: bool = True,
    ) -> None:
        self.present = present
        self.counts = counts or {}
        self.dialog_closes = dialog_closes
        self.clicks: list[str] = []
        self.escapes = 0
        self.screenshots: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def wait_for_timeout(self, ms: float) -> None:
        del ms

    async def screenshot(self, *, path: str, full_page: bool = False) -> None:
        del full_page
        self.screenshots.append(path)
        Path(path).write_bytes(b"png")

    @property
    def keyboard(self) -> Any:
        page = self

        class _Keyboard:
            async def press(self, key: str) -> None:
                if key == "Escape":
                    page.escapes += 1

        return _Keyboard()


# The tiers the happy path is expected to land on. Tier 1 (the structural Radix
# id) is the one gflow WANTS to hit; the localized tier is exercised separately.
_STRUCTURAL_AVATAR_TAB = AVATAR_TAB_SELECTORS[0]
_LOCALIZED_AVATAR_TAB = AVATAR_TAB_SELECTORS[-1]
_INCLUDE_TEXT_TIER, _INCLUDE_STRUCTURAL_TIER = PICKER_INCLUDE_BUTTON


def _healthy_page(*, tab: str = _STRUCTURAL_AVATAR_TAB, **kwargs: Any) -> _FakePage:
    return _FakePage(
        present={ADD_MEDIA_BUTTON, tab, _INCLUDE_TEXT_TIER},
        counts={PICKER_ANY_TAB: 3},
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: no announcement overlay in the way.

    Set explicitly rather than left to chance — ``_probe_selector_cascade``
    consults the overlay state on every miss, and an unstubbed probe would hit
    the fake page's ``evaluate``.
    """
    monkeypatch.setattr(
        UiAutomationTransport, "_overlay_blocks_page", AsyncMock(return_value=False)
    )


class TestAttachLikenessHappyPath:
    @pytest.mark.asyncio
    async def test_opens_add_media_then_avatar_tab_then_includes_in_order(self) -> None:
        page = _healthy_page()

        await VideoGenerationMixin._attach_likeness(page, out_dir=None)  # type: ignore[arg-type]

        # The ORDER is the contract: the tab is not clickable before the dialog
        # opens, and the include action is not present before the tab is chosen.
        assert page.clicks == [
            ADD_MEDIA_BUTTON,
            _STRUCTURAL_AVATAR_TAB,
            _INCLUDE_TEXT_TIER,
        ]
        # A successful attach leaves no dialog behind and needs no escape hatch.
        assert page.escapes == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_the_localized_tab_tier(self) -> None:
        """A non-English account with no structural id must still attach."""
        page = _healthy_page(tab=_LOCALIZED_AVATAR_TAB)

        await VideoGenerationMixin._attach_likeness(page, out_dir=None)  # type: ignore[arg-type]

        assert page.clicks[1] == _LOCALIZED_AVATAR_TAB

    @pytest.mark.asyncio
    async def test_falls_back_to_the_structural_include_tier(self) -> None:
        """#170's lesson: the include caption is localized, so the iconless-button
        tier must carry accounts the text tier misses."""
        page = _FakePage(
            present={ADD_MEDIA_BUTTON, _STRUCTURAL_AVATAR_TAB, _INCLUDE_STRUCTURAL_TIER},
            counts={PICKER_ANY_TAB: 3},
        )

        await VideoGenerationMixin._attach_likeness(page, out_dir=None)  # type: ignore[arg-type]

        assert page.clicks[-1] == _INCLUDE_STRUCTURAL_TIER

    @pytest.mark.asyncio
    async def test_no_english_only_tab_tier_leads_the_cascade(self) -> None:
        """A locale-dependent selector must never be tier 1 — that is how #170
        and #56 broke every non-English account."""
        assert "has-text" not in AVATAR_TAB_SELECTORS[0]
        assert "Avatar" not in AVATAR_TAB_SELECTORS[0]
        # ...and the localized tier, where it exists, is not English-only.
        localized = [s for s in AVATAR_TAB_SELECTORS if "has-text" in s]
        assert localized, "expected at least one bounded localized fallback"
        for sel in localized:
            assert "アバター" in sel or "Аватар" in sel


class TestAttachLikenessFailsBeforeSubmission:
    @pytest.mark.asyncio
    async def test_missing_avatar_tab_with_other_tabs_is_avatar_unavailable(
        self, tmp_path: Path
    ) -> None:
        """The picker opened and drew its other tabs, so the picker is fine —
        this account simply has no Avatar. No gflow release fixes that, so it
        must not be reported as selector drift."""
        page = _FakePage(present={ADD_MEDIA_BUTTON}, counts={PICKER_ANY_TAB: 3})

        with pytest.raises(AvatarUnavailableError) as excinfo:
            await VideoGenerationMixin._attach_likeness(page, out_dir=tmp_path)  # type: ignore[arg-type]

        assert "3 tab(s)" in excinfo.value.detail
        assert "no credits were spent" in excinfo.value.detail
        # Diagnostic bundle captured, and the page is left clean for the pool.
        assert page.screenshots and page.screenshots[0].endswith("debug_no_avatar_tab.png")
        assert page.escapes == 2
        # Nothing beyond opening the dialog was clicked — no include, no submit.
        assert page.clicks == [ADD_MEDIA_BUTTON]

    @pytest.mark.asyncio
    async def test_avatar_unavailable_is_not_retryable(self) -> None:
        """A region verdict answers identically on a re-run; advertising it as
        retryable would send users in circles."""
        from gflow_cli.errors import is_retryable

        assert is_retryable(AvatarUnavailableError("x")) is False

    @pytest.mark.asyncio
    async def test_picker_with_no_tabs_at_all_is_selector_drift(self, tmp_path: Path) -> None:
        """No tabs anywhere means the picker's own structure changed — that IS a
        gflow problem, and must not be blamed on the user's region."""
        page = _FakePage(present={ADD_MEDIA_BUTTON}, counts={PICKER_ANY_TAB: 0})

        with pytest.raises(UiSelectorDriftError) as excinfo:
            await VideoGenerationMixin._attach_likeness(page, out_dir=tmp_path)  # type: ignore[arg-type]

        assert "avatar_tab" in excinfo.value.detail
        assert page.escapes == 2

    @pytest.mark.asyncio
    async def test_missing_add_media_button_is_selector_drift(self, tmp_path: Path) -> None:
        page = _FakePage(present=set())

        with pytest.raises(UiSelectorDriftError) as excinfo:
            await VideoGenerationMixin._attach_likeness(page, out_dir=tmp_path)  # type: ignore[arg-type]

        assert "avatar_add_media" in excinfo.value.detail
        assert page.clicks == []

    @pytest.mark.asyncio
    async def test_picker_that_never_closes_means_the_attach_did_not_register(
        self, tmp_path: Path
    ) -> None:
        """Clicking the include button is not evidence; Flow closing the picker
        is. Without it we must NOT proceed to a submit."""
        page = _healthy_page(dialog_closes=False)

        with pytest.raises(TransportTimeoutError) as excinfo:
            await VideoGenerationMixin._attach_likeness(page, out_dir=tmp_path)  # type: ignore[arg-type]

        assert "likeness was not attached" in str(excinfo.value)
        assert "aborted before submitting" in str(excinfo.value)
        assert page.escapes == 2

    @pytest.mark.asyncio
    async def test_missing_include_action_raises_before_any_submit(self, tmp_path: Path) -> None:
        page = _FakePage(
            present={ADD_MEDIA_BUTTON, _STRUCTURAL_AVATAR_TAB},
            counts={PICKER_ANY_TAB: 3},
        )

        with pytest.raises(TransportTimeoutError):
            await VideoGenerationMixin._attach_likeness(page, out_dir=tmp_path)  # type: ignore[arg-type]

        assert _INCLUDE_TEXT_TIER not in page.clicks

    @pytest.mark.asyncio
    async def test_a_missing_control_is_checked_against_the_overlay_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#593: an announcement modal can cover a control that is present and
        fine. Every avatar probe must inherit that recovery rather than believe
        the first miss."""
        blocks = AsyncMock(return_value=False)
        monkeypatch.setattr(UiAutomationTransport, "_overlay_blocks_page", blocks)
        page = _FakePage(present=set())

        with pytest.raises(UiSelectorDriftError):
            await VideoGenerationMixin._attach_likeness(page, out_dir=None)  # type: ignore[arg-type]

        blocks.assert_awaited()

    @pytest.mark.asyncio
    async def test_an_overlay_is_dismissed_and_the_probe_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the announcement IS up, the control must be re-probed after
        dismissal — not reported as missing."""
        monkeypatch.setattr(
            UiAutomationTransport, "_overlay_blocks_page", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            UiAutomationTransport, "_changelog_overlay_present", AsyncMock(return_value=True)
        )
        page = _healthy_page()
        # The Add Media button only becomes visible once the overlay is gone.
        page.present.discard(ADD_MEDIA_BUTTON)

        async def _dismiss(_page: Any, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            page.present.add(ADD_MEDIA_BUTTON)
            return True

        monkeypatch.setattr(UiAutomationTransport, "_dismiss_blocking_overlays", _dismiss)

        await VideoGenerationMixin._attach_likeness(page, out_dir=None)  # type: ignore[arg-type]

        assert page.clicks[0] == ADD_MEDIA_BUTTON


# ---------------------------------------------------------------------------
# Orchestration: which sub-mode, which attaches, in what order
# ---------------------------------------------------------------------------


def _mock_async_page() -> MagicMock:
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.bring_to_front = AsyncMock()
    page.remove_listener = MagicMock()
    return page


def _stub_video_pipeline(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], *, generate_url: str = _T2V_URL
) -> None:
    """Stub every helper `generate_video` drives, recording attach ORDER."""
    from gflow_cli.api.transports.drivers import factory
    from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver

    generate_resp = {"status": 200, "url": generate_url, "body": {"media": [{"name": "v"}]}}

    async def _record(name: str) -> None:
        calls.append(name)

    async def _sub_mode(page: Any, sub: str, *, out_dir: Any) -> None:
        del page, out_dir
        calls.append(f"sub_mode:{sub}")

    async def _attach_likeness(page: Any, *, out_dir: Any) -> None:
        del page, out_dir
        await _record("likeness")

    async def _attach_references(page: Any, paths: Any, *, out_dir: Any, **kw: Any) -> None:
        del page, out_dir, kw
        calls.append(f"references:{len(list(paths))}")

    async def _attach_frame(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        await _record("frame")

    for name, stub in (
        ("_wait_video_editor_ready", AsyncMock()),
        ("_switch_to_video_mode", AsyncMock()),
        ("_set_output_count", AsyncMock()),
        ("_select_video_model", AsyncMock()),
        ("_select_video_duration", AsyncMock()),
        ("_select_video_aspect", AsyncMock()),
        ("_attach_remote_references", AsyncMock()),
        ("_attach_character_entities", AsyncMock()),
    ):
        monkeypatch.setattr(VideoGenerationMixin, name, stub)
    monkeypatch.setattr(VideoGenerationMixin, "_switch_video_sub_mode", staticmethod(_sub_mode))
    monkeypatch.setattr(VideoGenerationMixin, "_attach_likeness", staticmethod(_attach_likeness))
    monkeypatch.setattr(
        VideoGenerationMixin, "_attach_references", staticmethod(_attach_references)
    )
    monkeypatch.setattr(VideoGenerationMixin, "_attach_frame", staticmethod(_attach_frame))
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_video_response_listener",
        staticmethod(lambda page: ([generate_resp], object())),
    )
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_status_response_listener",
        staticmethod(lambda page: ([], object())),
    )
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_poll_video_status",
        AsyncMock(
            return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        ),
    )

    async def _bind_classic(page: Any, *, ui_mode: Any, transport: Any) -> Any:
        del page, ui_mode
        return ClassicFlowUiDriver(transport=transport)

    monkeypatch.setattr(factory, "get_ui_driver", _bind_classic)


def _transport_with_stubs(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], *, generate_url: str = _T2V_URL
) -> Any:
    transport = UiAutomationTransport()
    transport._page = _mock_async_page()
    transport._setup_done = True
    monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
    monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())

    async def _send_prompt(page: Any, text: str, out_dir: Any = None) -> None:
        del page, text, out_dir
        calls.append("submit")

    monkeypatch.setattr(transport, "_send_prompt", _send_prompt)
    monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
    _stub_video_pipeline(monkeypatch, calls, generate_url=generate_url)
    return transport


class TestPureAvatarVideoOrchestration:
    @pytest.mark.asyncio
    async def test_enters_references_submode_attaches_likeness_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 2026-07-01 finding: the Add Media button is not rendered on the
        bare Video tab, so pure avatar needs R2V's sub-mode switch too — and
        that switch must happen BEFORE the likeness attach."""
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="walk", mode=Mode.AVATAR),
            download=False,
        )

        assert calls == ["sub_mode:references", "likeness", "submit"]

    @pytest.mark.asyncio
    async def test_pure_avatar_never_touches_frames_or_references(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="walk", mode=Mode.AVATAR),
            download=False,
        )

        assert "frame" not in calls
        assert not any(c.startswith("references:") for c in calls)
        assert "sub_mode:frames" not in calls

    @pytest.mark.asyncio
    async def test_overlays_are_dismissed_before_the_composer_is_driven(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)
        dismiss = AsyncMock()
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", dismiss)

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="walk", mode=Mode.AVATAR),
            download=False,
        )

        assert dismiss.await_count >= 1


class TestR2VPlusAvatarOrchestration:
    @pytest.mark.asyncio
    async def test_attaches_references_then_likeness_in_one_references_submode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Both attach paths must run — neither replaces the other — and the
        editor must enter the references sub-mode exactly once."""
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)
        ref = tmp_path / "subject.png"
        ref.write_bytes(b"x")

        await transport.generate_video(
            request=GenerateVideoRequest(
                prompt="walking with the referenced subjects",
                mode=Mode.R2V,
                reference_images=(ref,),
                model=VideoModel.OMNI_FLASH,
                use_avatar=True,
            ),
            download=False,
        )

        assert calls == ["sub_mode:references", "references:1", "likeness", "submit"]
        assert calls.count("sub_mode:references") == 1


class TestNonAvatarPathsAreUnchanged:
    @pytest.mark.asyncio
    async def test_t2v_attaches_nothing_and_enters_no_submode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="a sunset"), download=False
        )

        assert calls == ["submit"]

    @pytest.mark.asyncio
    async def test_r2v_without_avatar_attaches_only_references(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls)
        ref = tmp_path / "a.png"
        ref.write_bytes(b"x")

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="p", mode=Mode.R2V, reference_images=(ref,)),
            download=False,
        )

        assert calls == ["sub_mode:references", "references:1", "submit"]

    @pytest.mark.asyncio
    async def test_i2v_still_uses_the_frames_submode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[str] = []
        transport = _transport_with_stubs(monkeypatch, calls, generate_url=_I2V_URL)
        frame = tmp_path / "start.png"
        frame.write_bytes(b"x")

        await transport.generate_video(
            request=GenerateVideoRequest(prompt="p", mode=Mode.I2V, start_image=frame),
            download=False,
        )

        assert calls == ["sub_mode:frames", "frame", "submit"]
        assert "likeness" not in calls


# ---------------------------------------------------------------------------
# Image path: same picker, no sub-mode, classic arm required
# ---------------------------------------------------------------------------


class TestImageAvatarOrchestration:
    @staticmethod
    def _image_transport(
        monkeypatch: pytest.MonkeyPatch, calls: list[str]
    ) -> tuple[Any, list[Any]]:
        from gflow_cli.api.transports.drivers import factory
        from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver

        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._page.url = "https://labs.google/fx/tools/flow/project/p1"
        transport._setup_done = True
        transport._out_dir = None
        bound_modes: list[Any] = []

        async def _bind(page: Any, *, ui_mode: Any, transport: Any) -> Any:
            del page
            bound_modes.append(ui_mode)
            return ClassicFlowUiDriver(transport=transport)

        async def _likeness(page: Any, *, out_dir: Any) -> None:
            del page, out_dir
            calls.append("likeness")

        async def _refs(page: Any, paths: Any, *, out_dir: Any, **kw: Any) -> None:
            del page, out_dir, kw
            calls.append(f"references:{len(list(paths))}")

        async def _send(page: Any, text: str, out_dir: Any = None) -> None:
            del page, text, out_dir
            calls.append("submit")

        monkeypatch.setattr(factory, "get_ui_driver", _bind)
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", _send)
        monkeypatch.setattr(UiAutomationTransport, "_switch_to_image_mode", AsyncMock())
        monkeypatch.setattr(UiAutomationTransport, "_configure_generation_settings", AsyncMock())
        monkeypatch.setattr(VideoGenerationMixin, "_attach_likeness", staticmethod(_likeness))
        monkeypatch.setattr(VideoGenerationMixin, "_attach_references", staticmethod(_refs))
        monkeypatch.setattr(
            transport,
            "_attach_batch_response_listener",
            lambda page, project_id=None: ([], lambda: None),
        )
        monkeypatch.setattr(
            transport,
            "_attach_batch_request_logger",
            lambda page, project_id=None, sink=None, record_generation_request=None: lambda: None,
        )
        monkeypatch.setattr(transport, "_await_captured", AsyncMock(return_value=[{"body": {}}]))
        monkeypatch.setattr(
            "gflow_cli.api.transports.ui_automation._images_from_responses",
            lambda responses: ([object()], None, None, None),
        )
        return transport, bound_modes

    @pytest.mark.asyncio
    async def test_image_avatar_attaches_the_likeness_before_submitting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.image import GenerateImageRequest

        calls: list[str] = []
        transport, _ = self._image_transport(monkeypatch, calls)

        await transport._generate_images_locked(
            GenerateImageRequest(prompt="cinematic portrait", use_avatar=True),
            project_id="p1",
        )

        assert calls == ["likeness", "submit"]

    @pytest.mark.asyncio
    async def test_image_avatar_requires_the_classic_arm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The classic Add-Media picker is the only place the likeness lives, so
        the arm bind must REQUIRE classic — even when the environment asks for
        agentic — rather than failing later on a button the agentic UI never
        draws."""
        from gflow_cli.api.image import GenerateImageRequest
        from gflow_cli.config import UiMode

        calls: list[str] = []
        transport, bound_modes = self._image_transport(monkeypatch, calls)
        monkeypatch.setattr("gflow_cli.config.resolve_ui_mode", lambda _: UiMode.AGENTIC)

        await transport._generate_images_locked(
            GenerateImageRequest(prompt="p", use_avatar=True), project_id="p1"
        )

        assert bound_modes == [UiMode.CLASSIC]

    @pytest.mark.asyncio
    async def test_plain_t2i_attaches_nothing_and_keeps_its_arm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.image import GenerateImageRequest
        from gflow_cli.config import UiMode

        calls: list[str] = []
        transport, bound_modes = self._image_transport(monkeypatch, calls)
        monkeypatch.setattr("gflow_cli.config.resolve_ui_mode", lambda _: UiMode.AGENTIC)

        await transport._generate_images_locked(
            GenerateImageRequest(prompt="a forest"), project_id="p1"
        )

        assert calls == ["submit"]
        assert bound_modes == [UiMode.AGENTIC]

    @pytest.mark.asyncio
    async def test_i2i_reference_attach_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from gflow_cli.api.image import GenerateImageRequest

        ref = tmp_path / "a.png"
        ref.write_bytes(b"x")
        calls: list[str] = []
        transport, _ = self._image_transport(monkeypatch, calls)

        await transport._generate_images_locked(
            GenerateImageRequest(prompt="p", ref_paths=(ref,)), project_id="p1"
        )

        assert calls == ["references:1", "submit"]
        assert "likeness" not in calls
