"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.config import UiMode
    from gflow_cli.tools.invocation import AppliedTool

# Type alias used with cast() in response parsers — avoids repeating the
# string-form annotation "dict[str, Any]" on every call (SonarCloud S1192).
_StrAnyDict = dict[str, Any]


class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    # AVATAR = prompt + the account's own Flow Avatar/likeness, with NO image
    # inputs of any kind. It is a distinct Mode rather than a flag on T2V for
    # three reasons that all bite at once:
    #   * the transport must switch the editor into the References/Ingredients
    #     sub-mode (the Add-Media button is not rendered on the bare Video tab),
    #     which is a per-mode decision the classic driver already makes;
    #   * `OperationKind(request.mode.value)` is how every video operation is
    #     recorded, so a T2V-shaped avatar run would be catalogued as `t2v` and
    #     lose its provenance; and
    #   * "plain T2V must reject an avatar flag" is only expressible if T2V and
    #     avatar are different modes.
    # Avatar + reference images is NOT this mode — that is `Mode.R2V` with
    # ``use_avatar=True`` (Flow carries `referenceLikenesses` alongside
    # `referenceImages` in one request).
    AVATAR = "avatar"


class Tier(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class VideoModel(StrEnum):
    """Flow video model, as exposed in the editor's model picker.

    Verified live (flow-editor-map.json): the picker offers exactly these five.
    The selector for each lives in the transport layer (this module is pure —
    no DOM knowledge).

    **Capability and cohort history (refs #451, #288, PR #650):**
    Flow's UI exposure of duration controls is account- and cohort-dependent.
    Historical captures (2026-08-14, two accounts/locales — see
    docs/superpowers/spikes/2026-08-14-video-model-capability-matrix.md) found
    no duration control rendered for Veo models, leading to issues #451/#288
    where explicit duration failed to select a tab. A subsequent live capture
    (2026-09-04, PR #650, labs.google) confirmed that on cohorts where the
    duration row is rendered for Veo, `veo_3_1_lite`, `veo_3_1_fast`, and
    `veo_3_1_quality` expose 4s, 6s, and 8s tabs, while 10s remains exclusive
    to `omni_flash`. (`veo_3_1_lite_lower_priority` returned a picker miss in
    that capture, so no positive live-UI claim is made for it.) On cohorts
    without the control, explicit `--duration` cannot be applied and users
    must accept Flow's default.
    """

    OMNI_FLASH = "omni_flash"
    VEO_3_1_LITE = "veo_3_1_lite"
    VEO_3_1_FAST = "veo_3_1_fast"
    VEO_3_1_QUALITY = "veo_3_1_quality"
    VEO_3_1_LITE_LOWER_PRIORITY = "veo_3_1_lite_lower_priority"

    @classmethod
    def from_cli(cls, value: str | None) -> VideoModel | None:
        """Map a friendly CLI alias to the model. ``None`` -> ``None`` (use
        Flow's UI default — the picker is not touched)."""
        if value is None:
            return None
        key = value.strip().lower().replace("-", "_").replace(" ", "_")
        if key not in _VIDEO_MODEL_FROM_CLI:
            msg = (
                f"Unknown video model {value!r}; choose from "
                f"{sorted({m.value for m in cls})} or aliases {sorted(_VIDEO_MODEL_FROM_CLI)}"
            )
            raise ValueError(
                msg,
            )
        return _VIDEO_MODEL_FROM_CLI[key]


# Default model for ``gflow video i2v`` and direct ``FlowApiClient.generate_video``
# callers when ``model`` is omitted and the request carries a start/end frame.
# ``veo_3_1_lite`` stays the default because it is the cheapest model, not
# because of any capability edge: since Flow shipped first+last for Omni 1.1
# Flash, EVERY model carries both start-only and start+end i2v (#626), so
# ``omni_flash`` (10s capable) is a plain opt-in via an explicit ``--model``.
I2V_DEFAULT_MODEL: VideoModel = VideoModel.VEO_3_1_LITE


# Module-level alias map — friendly CLI strings -> VideoModel. Hoisted out of
# `VideoModel.from_cli` (defined after the class so the members resolve) so
# `gflow models` can enumerate the aliases without duplicating them.
_VIDEO_MODEL_FROM_CLI: dict[str, VideoModel] = {
    "omni_flash": VideoModel.OMNI_FLASH,
    "omni": VideoModel.OMNI_FLASH,
    "flash": VideoModel.OMNI_FLASH,
    "veo_3_1_lite": VideoModel.VEO_3_1_LITE,
    "veo_lite": VideoModel.VEO_3_1_LITE,
    "lite": VideoModel.VEO_3_1_LITE,
    "veo_3_1_fast": VideoModel.VEO_3_1_FAST,
    "veo_fast": VideoModel.VEO_3_1_FAST,
    "fast": VideoModel.VEO_3_1_FAST,
    "veo_3_1_quality": VideoModel.VEO_3_1_QUALITY,
    "veo_quality": VideoModel.VEO_3_1_QUALITY,
    "quality": VideoModel.VEO_3_1_QUALITY,
    "veo_3_1_lite_lower_priority": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "veo_lite_lp": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "lite_lp": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "lower_priority": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
}


class Aspect(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"

    def wire(self) -> str:
        return f"VIDEO_ASPECT_RATIO_{self.value.upper()}"

    @classmethod
    def from_cli(cls, value: str) -> Aspect:
        mapping = {"9:16": cls.PORTRAIT, "16:9": cls.LANDSCAPE, "1:1": cls.SQUARE}
        if value not in mapping:
            msg = f"Unsupported aspect ratio {value!r}; choose from {sorted(mapping)}"
            raise ValueError(msg)
        return mapping[value]


# Flow's R2V reference cap is MODEL-DEPENDENT (live-verified + Google's
# official support page): omni_flash allows 7 ("Maximum image ingredients
# reached (7 allowed)"); veo_3_1_lite / veo_3_1_fast / veo_3_1_lite_lower_priority
# allow 3; veo_3_1_quality does NOT support R2V at all (Google Flow help page
# "Ingredients/References to Video" row = "No"). A request that exceeds the cap
# uploads all refs but the generate call silently keeps only N — so we reject
# up front. MAX_REFERENCE_IMAGES is the absolute ceiling (omni) used when the
# model is unknown; the model-aware check below enforces the exact per-model
# limit (incl. the special cap=0 for QUALITY) when the model is known.
MAX_REFERENCE_IMAGES = 7
_VIDEO_REFERENCE_CAP: Mapping[VideoModel, int] = MappingProxyType(
    {
        VideoModel.OMNI_FLASH: 7,
        VideoModel.VEO_3_1_LITE: 3,
        VideoModel.VEO_3_1_FAST: 3,
        VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: 3,
        VideoModel.VEO_3_1_QUALITY: 0,  # R2V unsupported per Google Flow docs
    },
)


def reference_cap_for(model: VideoModel) -> int:
    """Maximum number of R2V reference images *model* accepts.

    Returns 0 for models that do not support R2V at all
    (``VEO_3_1_QUALITY`` — per Google Flow's official support page, and
    confirmed live 2026-08-14 on two accounts: selecting Veo 3.1 - Quality greys
    an attached ingredient with "You cannot use image ingredients with this
    model", while Omni Flash / Fast / Lite accept the same asset. A cap of 0 is
    therefore also the answer to "does this model take image ingredients?" —
    there is deliberately no second predicate encoding the same rule).

    Ordering note: Flow flags an ingredient attached BEFORE the model was
    switched, so the model must be chosen first. The transport already does this
    (``configure_video_settings`` runs before ``_attach_media_inputs``).
    Unknown/future models fall back to :data:`MAX_REFERENCE_IMAGES` rather than
    raising, so adding a new ``VideoModel`` member without a cap entry degrades
    to the ceiling instead of a ``KeyError`` at request-build time.
    """
    return _VIDEO_REFERENCE_CAP.get(model, MAX_REFERENCE_IMAGES)


def model_aliases(model: VideoModel) -> list[str]:
    """Sorted CLI aliases that resolve to *model* (for `gflow models`)."""
    return sorted(alias for alias, m in _VIDEO_MODEL_FROM_CLI.items() if m is model)


VIDEO_DURATION_CHOICES: tuple[int, ...] = (4, 6, 8, 10)


def max_duration_for(model: VideoModel) -> int:
    """Maximum selectable clip length in seconds for *model*."""
    return 10 if model is VideoModel.OMNI_FLASH else 8


def validate_duration_for_model(model: VideoModel, duration: int | str | None) -> None:
    """Validate one duration against the canonical Flow model limits."""
    if duration is None:
        return
    try:
        seconds = int(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"duration must be one of 4/6/8/10 seconds, got {duration}") from exc
    if seconds not in VIDEO_DURATION_CHOICES:
        raise ValueError(f"duration must be one of 4/6/8/10 seconds, got {duration}")
    maximum = max_duration_for(model)
    if seconds > maximum:
        raise ValueError(
            f"model {model.value!r} caps at {maximum}s; duration {seconds} is only available "
            f"for omni_flash"
        )


# Case-insensitive 8-4-4-4-12 hex with hyphens — Flow's media UUIDs (the same
# shape cli_image's --ref classifier accepts).
_MEDIA_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_media_uuid(value: str) -> bool:
    """True when *value* is shaped like a Flow media UUID (#287)."""
    return _MEDIA_UUID_RE.fullmatch(value) is not None


def aspect_choices() -> dict[str, str]:
    """Map each accepted CLI aspect ratio to its wire value."""
    return {
        "9:16": Aspect.PORTRAIT.wire(),
        "16:9": Aspect.LANDSCAPE.wire(),
        "1:1": Aspect.SQUARE.wire(),
    }


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Inputs for ONE video generation. Mode is explicit; image inputs are
    local file paths the transport attaches through Flow's catalog UI.

    `__post_init__` validates STRUCTURE only — it does not check that image
    paths exist on disk (that is I/O; this module is pure). Path existence is
    validated by the transport at the boundary.
    """

    prompt: str
    mode: Mode = Mode.T2V
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST  # meaningful for T2V only — I2V/R2V model keys are fixed
    model: VideoModel | None = None  # None -> Flow UI default (picker untouched)
    duration: int | None = None  # seconds: 4/6/8 (or 10, omni_flash only); None -> default
    count: int = 1  # 1-4 outputs; >1 multiplies credit cost
    seed: int | None = None
    start_image: Path | None = None  # I2V (local file path)
    start_image_ref_name: str | None = None  # I2V (remote asset display name)
    start_image_ref_id: str | None = None  # I2V (in-project asset media UUID, #287)
    end_image: Path | None = None  # I2V (optional local file path)
    end_image_ref_name: str | None = None  # I2V (optional remote asset display name)
    end_image_ref_id: str | None = None  # I2V (optional in-project asset media UUID, #287)
    # Catalog-resolved names used only to filter the browser picker for UUID
    # refs. The UUID fields above remain the exact asset identity. Empty names
    # remain valid for direct/legacy callers only when paired with a verified
    # recorded local-image fallback; the transport never scans by UUID.
    start_image_ref_display_name: str = ""
    end_image_ref_display_name: str = ""
    start_image_ref_local_path: Path | None = None
    end_image_ref_local_path: Path | None = None
    start_image_ref_local_sha256: str = ""
    end_image_ref_local_sha256: str = ""
    # Display name of the target project for the media picker's project-menu
    # match (#287: the menu lists projects by NAME, not id). Optional override
    # (--project-name / GFLOW_CLI_PROJECT_NAME); when None the transport
    # derives a name from the live page.
    project_name: str | None = None
    reference_images: tuple[Path, ...] = ()  # R2V (local file paths)
    ref_names: tuple[str, ...] = ()  # R2V (remote asset display names)
    reference_entities: tuple[str, ...] = ()  # R2V — Flow CHARACTER entity ids
    reference_entity_names: tuple[
        str, ...
    ] = ()  # R2V — character DISPLAY names (UI picker selection)
    reference_audio: str | None = None  # R2V — voice resource mediaId (e.g. "alnilam")
    # Tool provenance (recorded, never sent on the wire). ``original_prompt`` is
    # the user's pre-tool text when a ``--tool`` rewrote ``prompt``; ``tool`` is
    # the applied-tool snapshot for ``operations.metadata_json.tool``. (PR2 §8)
    original_prompt: str | None = None
    tool: AppliedTool | None = None
    # Requested Flow UI arm (#299) from --ui-mode; None → resolve from
    # GFLOW_CLI_UI_MODE / default at the transport. Not sent on the wire.
    # The video pipeline clamps to classic-required (no agentic video driver
    # exists); see _generate_video_locked.
    ui_mode: UiMode | None = None
    # Attach the account's Flow Avatar/likeness through the editor's Add-Media
    # dialog. gflow never forges `referenceLikenesses` itself — the UI click is
    # what makes Flow's own JS put it on the wire.
    #
    # SINGLE source of truth for "this request wants the likeness": ``Mode.AVATAR``
    # NORMALISES this to True in __post_init__ (see :meth:`_normalise_avatar`), so
    # the pair can never disagree and there is no reachable
    # ``Mode.AVATAR + use_avatar=False`` state. Valid on ``Mode.R2V`` (references
    # + likeness); REJECTED on ``Mode.T2V`` and ``Mode.I2V``.
    use_avatar: bool = False

    def __post_init__(self) -> None:
        self._normalise_avatar()
        self._validate_prompt()
        self._validate_duration()
        self._validate_count()
        self._validate_frame_ref_ids()
        self._validate_mode_symmetry()
        self._validate_avatar()
        self._validate_r2v_caps()
        self._validate_model_capabilities()
        self._validate_seed()
        self._validate_ui_mode()

    def _normalise_avatar(self) -> None:
        """``Mode.AVATAR`` implies the likeness — collapse the two-field state.

        Done as normalisation rather than validation on purpose: rejecting
        ``Mode.AVATAR + use_avatar=False`` would make callers repeat themselves,
        while ACCEPTING it would leave an avatar mode that attaches nothing.
        Forcing the flag makes the invalid combination unrepresentable, so every
        downstream branch can read one field (:attr:`attaches_likeness`).
        """
        if self.mode is Mode.AVATAR and not self.use_avatar:
            object.__setattr__(self, "use_avatar", True)

    @property
    def attaches_likeness(self) -> bool:
        """True when the transport must attach the account Avatar/likeness.

        The one predicate the driver, the transport and the recorder branch on,
        so "pure avatar" and "r2v + avatar" cannot drift apart across the three.
        """
        return self.use_avatar

    def _validate_avatar(self) -> None:
        """Avatar is only expressible on ``Mode.AVATAR`` and ``Mode.R2V``.

        * ``Mode.T2V`` — a plain text-to-video request that also carries an avatar
          flag is a caller bug, not a request Flow can serve: T2V never enters the
          References sub-mode, so the likeness would be silently dropped and the
          user would be billed for a generation that ignored their subject.
        * ``Mode.I2V`` — Flow's own UI does not offer the Avatar surface once the
          composer is in the Frames sub-mode, and no capture proves a start/end
          frame can coexist with ``referenceLikenesses``. Refused rather than
          guessed; if a future capture proves the combination, relax THIS branch
          (and only this branch) with the evidence in the commit.
        * ``Mode.AVATAR`` — pure: prompt + likeness, no image inputs at all. The
          frame/reference exclusions live here rather than in
          ``_validate_mode_symmetry`` so the message can name the R2V escape hatch.
        """
        if self.use_avatar and self.mode is Mode.T2V:
            msg = (
                "T2V request must not carry use_avatar; use Mode.AVATAR for a "
                "prompt + avatar video, or Mode.R2V with use_avatar=True to "
                "combine reference images with the avatar"
            )
            raise ValueError(msg)
        if self.use_avatar and self.mode is Mode.I2V:
            msg = (
                "I2V request must not carry use_avatar: Flow does not expose the "
                "Avatar surface in the Frames sub-mode, and no capture proves a "
                "start/end frame can coexist with referenceLikenesses"
            )
            raise ValueError(msg)
        if self.mode is not Mode.AVATAR:
            return
        if self._has_frame_input():
            msg = "AVATAR request must not carry start/end images"
            raise ValueError(msg)
        if self.reference_images or self.ref_names or self.reference_entities:
            msg = (
                "AVATAR request must not carry reference images, ref names, or "
                "reference entities; use Mode.R2V with use_avatar=True to combine "
                "references with the avatar"
            )
            raise ValueError(msg)
        # Attaching the likeness requires the editor's References/Ingredients
        # sub-mode (the Add-Media button is not rendered on the bare Video tab —
        # this is exactly what the 2026-07-01 fix on the source branch found).
        # A model with a reference cap of 0 does not offer that workflow at all,
        # so the sub-mode switch would fail mid-flow as selector drift. Reuse the
        # SAME cap predicate R2V uses rather than a second "supports ingredients?"
        # rule — one source of truth (see reference_cap_for's docstring).
        # ``model is None`` leaves Flow's sticky UI default in play and is
        # genuinely unknowable here, exactly as for t2v/r2v.
        if self.model is not None and reference_cap_for(self.model) == 0:
            msg = (
                f"{self.model.value} does not support the references/ingredients "
                f"workflow the avatar attach requires; choose a model that does "
                f"(e.g. omni_flash, veo_3_1_lite, veo_3_1_fast)"
            )
            raise ValueError(msg)

    def _has_frame_input(self) -> bool:
        """True when the request carries an i2v start/end frame in any form."""
        return any(
            (
                self.start_image,
                self.start_image_ref_name,
                self.start_image_ref_id,
                self.end_image,
                self.end_image_ref_name,
                self.end_image_ref_id,
            )
        )

    def _validate_model_capabilities(self) -> None:
        """Reject model/feature combinations Flow's UI cannot express (#451/#288).

        Only runs when ``model`` is explicit: with ``model=None`` the picker is
        untouched and Flow's own default applies, so there is nothing to check
        against. Both branches fail HERE — at DTO construction, before any
        browser work — instead of surfacing later as a selector-drift timeout
        that blames the UI for a capability mismatch.
        """
        # Resolve the model the TRANSPORT will actually use, not just the one
        # the caller named. An i2v request with frames and no --model is bound
        # to I2V_DEFAULT_MODEL downstream (drivers/classic.py, _resolve_i2v_model),
        # so an unresolved `model is None` early-return let i2v's own DEFAULT
        # path keep hitting the exact #451/#288 failure this guard exists to
        # prevent. For t2v/r2v with no model, Flow's sticky UI default applies
        # and is genuinely unknowable here — those stay unguarded by design.
        effective = self.model
        if effective is None and self.mode is Mode.I2V and self._has_frame_input():
            effective = I2V_DEFAULT_MODEL
        if effective is None:
            return
        validate_duration_for_model(effective, self.duration)
        # NOTE: the ingredient x model case is deliberately NOT re-checked here.
        # ``_validate_r2v_caps`` already rejects it via ``reference_cap_for() == 0``
        # (VEO_3_1_QUALITY), with a cap-aware message. A second guard would be a
        # second source of truth for the same rule.

    def _validate_ui_mode(self) -> None:
        # #299: no agentic VIDEO driver exists — an explicit agentic request
        # must fail loudly at EVERY producer (the CLI/MCP edges reject earlier
        # with friendlier errors; this catches queue payloads and programmatic
        # use, where a silent classic clamp would spend credits on a render
        # the caller believes is agentic). Env-sourced agentic never reaches
        # the DTO (stays None) and degrades at the transport with a warning.
        if self.ui_mode is not None and self.ui_mode.value == "agentic":
            msg = (
                "ui_mode 'agentic' is not supported for video generation "
                "(no agentic video driver exists; refs #299)"
            )
            raise ValueError(msg)

    def _validate_frame_ref_ids(self) -> None:
        for slot, ref_id, display_name, local_path, local_sha256, alternatives in (
            (
                "start",
                self.start_image_ref_id,
                self.start_image_ref_display_name,
                self.start_image_ref_local_path,
                self.start_image_ref_local_sha256,
                (self.start_image, self.start_image_ref_name),
            ),
            (
                "end",
                self.end_image_ref_id,
                self.end_image_ref_display_name,
                self.end_image_ref_local_path,
                self.end_image_ref_local_sha256,
                (self.end_image, self.end_image_ref_name),
            ),
        ):
            if display_name and ref_id is None:
                msg = f"{slot}_image_ref_display_name requires {slot}_image_ref_id"
                raise ValueError(msg)
            if local_path is not None and ref_id is None:
                msg = f"{slot}_image_ref_local_path requires {slot}_image_ref_id"
                raise ValueError(msg)
            if local_path is not None and not local_sha256:
                msg = f"{slot}_image_ref_local_path requires {slot}_image_ref_local_sha256"
                raise ValueError(msg)
            if local_sha256 and local_path is None:
                msg = f"{slot}_image_ref_local_sha256 requires {slot}_image_ref_local_path"
                raise ValueError(msg)
            if ref_id is None:
                continue
            if not is_media_uuid(ref_id):
                msg = f"{slot}_image_ref_id {ref_id!r} is not a valid media UUID"
                raise ValueError(msg)
            if any(alt is not None for alt in alternatives):
                msg = (
                    f"at most one of {slot}_image, {slot}_image_ref_name, or "
                    f"{slot}_image_ref_id may be set"
                )
                raise ValueError(msg)

    def _validate_prompt(self) -> None:
        if not self.prompt.strip():
            msg = "prompt must not be empty"
            raise ValueError(msg)

    def _validate_duration(self) -> None:
        if self.duration is not None and self.duration not in VIDEO_DURATION_CHOICES:
            msg = f"duration must be one of 4/6/8/10 seconds, got {self.duration}"
            raise ValueError(msg)

    def _validate_count(self) -> None:
        if not (1 <= self.count <= 4):
            msg = f"count must be 1-4, got {self.count}"
            raise ValueError(msg)

    def _validate_i2v_symmetry(self) -> None:
        if (
            self.start_image is None
            and self.start_image_ref_name is None
            and self.start_image_ref_id is None
        ):
            msg = "I2V request requires start_image, start_image_ref_name, or start_image_ref_id"
            raise ValueError(msg)
        if self.reference_images or self.ref_names or self.reference_entities:
            msg = "I2V request must not carry reference_images, ref_names, or reference_entities"
            raise ValueError(msg)

    def _validate_r2v_symmetry(self) -> None:
        if not self.reference_images and not self.ref_names and not self.reference_entities:
            msg = "R2V request requires reference_images, ref_names, or reference_entities"
            raise ValueError(msg)
        if self._has_frame_input():
            msg = "R2V request must not carry start/end images"
            raise ValueError(msg)

    def _validate_mode_symmetry(self) -> None:
        if self.mode is Mode.T2V and (
            self._has_frame_input() or self.reference_images or self.ref_names
        ):
            msg = "T2V request must not carry image inputs"
            raise ValueError(msg)
        if self.mode is Mode.I2V:
            self._validate_i2v_symmetry()
        if self.mode is Mode.R2V:
            self._validate_r2v_symmetry()

    def _validate_r2v_caps(self) -> None:
        if len(self.reference_images) + len(self.ref_names) > MAX_REFERENCE_IMAGES:
            msg = f"at most {MAX_REFERENCE_IMAGES} reference images"
            raise ValueError(msg)
        # Per-model reference cap (live-verified): omni_flash=7, veo lite/fast/lite_lp=3,
        # veo_quality=0 (R2V unsupported per Google docs). When the model is None
        # (Flow UI default) we can't know it — leave the absolute ceiling above.
        if self.mode is Mode.R2V and self.model is not None:
            cap = reference_cap_for(self.model)
            if cap == 0:
                msg = f"{self.model.value} does not support R2V (reference-to-video)"
                raise ValueError(msg)
            total_img_refs = len(self.reference_images) + len(self.ref_names)
            if total_img_refs > cap:
                msg = (
                    f"{self.model.value} allows at most {cap} reference image(s); "
                    f"got {total_img_refs}"
                )
                raise ValueError(
                    msg,
                )
            total_refs = total_img_refs + len(self.reference_entities)
            if total_refs > cap:
                msg = (
                    f"reference cap exceeded: {total_refs} refs (images+entities) "
                    f"> {cap} for {self.model.value}"
                )
                raise ValueError(msg)

    def _validate_seed(self) -> None:
        if self.seed is not None and not (0 <= self.seed <= 2**31 - 1):
            msg = "seed out of range"
            raise ValueError(msg)


@dataclass(frozen=True)
class VideoStatus:
    """Terminal-or-not status of one in-flight video generation."""

    media_id: str
    status: str  # a MEDIA_GENERATION_STATUS_* wire value
    failure_reasons: tuple[str, ...] = ()
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "MEDIA_GENERATION_STATUS_FAILED",
        }

    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"


@dataclass(frozen=True)
class VideoStarted:
    """Fired as soon as a media_id/project_id/operation_id are known, BEFORE
    polling completes — allows a recorder to insert a STARTED row even if the
    long poll later fails.
    """

    media_id: str
    project_id: str | None = None
    flow_operation_id: str | None = None


@dataclass(frozen=True)
class VideoResult:
    """Return value of :meth:`generate_video` after Phase B download wiring.

    ``local_path`` is ``None`` when ``download=False`` was passed, or when
    the generation failed — callers should check ``status.succeeded`` first.

    ``project_id`` and ``flow_operation_id`` are populated by the transport
    when available, for use by the data-layer recorder (Task 8).
    """

    status: VideoStatus
    local_path: Path | None
    project_id: str | None = None
    flow_operation_id: str | None = None


# Callback type: invoked by the transport the moment a media_id becomes known,
# before polling completes. May be sync or async.
VideoStartedCallback = Callable[[VideoStarted], Awaitable[None] | None]


def operation_name_from_generate_response(response_json: dict[str, Any]) -> str | None:
    """Return the operation name from ``operations[0].operation.name`` in a
    batchAsyncGenerateVideo* response, or None if absent.

    The T2V response body carries both ``media[0].name`` AND
    ``operations[0].operation.name``. The spec stores them SEPARATELY even when
    they currently happen to match — use :func:`media_name_from_generate_response`
    for the media id and this function for the operation id.
    """
    operations = response_json.get("operations")
    if not isinstance(operations, list) or not operations:
        return None
    first: dict[str, Any] = cast(_StrAnyDict, operations[0])
    operation: dict[str, Any] | None = cast("dict[str, Any] | None", first.get("operation"))
    if not isinstance(operation, dict):
        return None
    name_val: str | None = cast("str | None", operation.get("name"))
    return name_val if name_val is not None else None


def media_name_from_generate_response(response_json: dict[str, Any]) -> str:
    """Return `media[0].name` from a batchAsyncGenerateVideo* response.

    Shapes: captures 02 (T2V), 08 (I2V), 09 (R2V). The T2V response also
    carries a top-level `operations[]`; this parser deliberately reads
    `media[0].name` (spec §2.4 — the candidate ids collapse to one uuid).
    """
    try:
        media = response_json["media"]
        return str(media[0]["name"])
    except (KeyError, IndexError, TypeError) as e:
        msg = f"generate response carries no media[0].name: {e}"
        raise ValueError(msg) from e


def parse_video_status(response_json: dict[str, Any], *, media_id: str) -> VideoStatus:
    """Parse one batchCheckAsyncVideoGenerationStatus response into a VideoStatus.

    Selects the `media[]` entry whose `name == media_id`, then reads
    `mediaMetadata.mediaStatus.{mediaGenerationStatus, failureReasons,
    error.message}`. Shapes: captures 10 (SUCCESSFUL), 11 (FAILED).
    Raises ValueError if `media_id` is absent or the status is malformed.
    """
    _media = response_json.get("media")
    if not isinstance(_media, list):
        msg = "status response has no media[] array"
        raise ValueError(msg)
    media: list[dict[str, Any]] = cast("list[dict[str, Any]]", _media)
    for item in media:
        if item.get("name") != media_id:
            continue
        meta = cast(_StrAnyDict, item.get("mediaMetadata") or {})
        media_status = cast(_StrAnyDict, meta.get("mediaStatus") or {})
        status = media_status.get("mediaGenerationStatus")
        if not isinstance(status, str):
            msg = f"status entry for {media_id} has no mediaGenerationStatus"
            raise ValueError(msg)
        reasons = tuple(cast("list[str]", media_status.get("failureReasons") or []))
        error_entry = cast(_StrAnyDict, media_status.get("error") or {})
        raw_msg = error_entry.get("message")
        error_message: str | None = str(raw_msg) if raw_msg is not None else None
        return VideoStatus(
            media_id=media_id,
            status=status,
            failure_reasons=reasons,
            error_message=error_message,
        )
    msg = f"media_id {media_id!r} not found in status response"
    raise ValueError(msg)
