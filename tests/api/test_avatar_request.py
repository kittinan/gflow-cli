"""Request-level contract for Avatar/likeness generation.

Every assertion here is about a combination the DTO must ACCEPT or REFUSE
*before* a browser opens, because the cheapest place to stop a bad avatar
request is the constructor. The transport-side behaviour lives in
``tests/api/transports/test_avatar_attach.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest, ImageRef
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.data.models import OperationKind


class TestImageAvatarRequest:
    def test_avatar_image_is_prompt_plus_likeness(self) -> None:
        req = GenerateImageRequest(prompt="cinematic portrait in Bangkok", use_avatar=True)

        assert req.use_avatar is True
        assert req.attaches_likeness is True
        assert req.refs == ()
        assert req.ref_paths == ()
        assert req.reference_entities == ()

    def test_plain_image_request_does_not_attach_likeness(self) -> None:
        assert GenerateImageRequest(prompt="a forest").attaches_likeness is False

    @pytest.mark.parametrize(
        ("kwargs", "what"),
        [
            ({"ref_paths": (Path("a.png"),)}, "local ref path"),
            (
                {"refs": (ImageRef("11111111-2222-3333-4444-555555555555"),)},
                "pre-uploaded media uuid",
            ),
            ({"reference_entities": ("ent-1",)}, "character entity"),
        ],
    )
    def test_avatar_rejects_every_other_reference_kind(
        self, kwargs: dict[str, object], what: str
    ) -> None:
        """No capture proves Flow accepts referenceLikenesses alongside
        imageInputs/referenceEntities, so the combination is refused rather than
        billed to the user to find out."""
        with pytest.raises(ValueError, match="use_avatar cannot be combined"):
            GenerateImageRequest(prompt="p", use_avatar=True, **kwargs)  # type: ignore[arg-type]
        # ...and the SAME request without the avatar flag stays legal, so the
        # guard is about the combination and not about the reference kind.
        assert GenerateImageRequest(prompt="p", **kwargs).attaches_likeness is False  # type: ignore[arg-type]

    def test_avatar_rejects_agent_instructions(self) -> None:
        """Instructions require the agentic arm; the avatar attach requires the
        classic composer. Unsatisfiable, so it fails at construction."""
        from gflow_cli.api.image import AgentInstruction

        with pytest.raises(ValueError, match="agent instructions"):
            GenerateImageRequest(
                prompt="p",
                use_avatar=True,
                instructions=(AgentInstruction(text="be moody"),),
            )

    def test_avatar_survives_aspect_model_and_count(self) -> None:
        req = GenerateImageRequest(
            prompt="p",
            use_avatar=True,
            aspect=ImageAspect.SQUARE,
            model=ImageModel.NARWHAL,
            count=4,
        )

        assert (req.attaches_likeness, req.count, req.aspect) is not None
        assert req.attaches_likeness and req.count == 4 and req.aspect is ImageAspect.SQUARE


class TestPureAvatarVideoRequest:
    def test_mode_avatar_normalises_the_flag(self) -> None:
        """Mode.AVATAR + use_avatar=False must be UNREPRESENTABLE, not merely
        rejected — otherwise an avatar mode could attach nothing."""
        req = GenerateVideoRequest(prompt="walking through Bangkok", mode=Mode.AVATAR)

        assert req.use_avatar is True
        assert req.attaches_likeness is True

    def test_explicit_flag_is_idempotent(self) -> None:
        req = GenerateVideoRequest(prompt="p", mode=Mode.AVATAR, use_avatar=True)

        assert req.use_avatar is True

    def test_avatar_mode_value_matches_the_recorded_operation_kind(self) -> None:
        """The recorder writes `OperationKind(request.mode.value)`, so these two
        enums are one contract, not two."""
        assert OperationKind(Mode.AVATAR.value) is OperationKind.AVATAR

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"start_image": Path("a.png")}, "start/end images"),
            ({"end_image": Path("b.png")}, "start/end images"),
            (
                {"start_image_ref_id": "11111111-2222-3333-4444-555555555555"},
                "start/end images",
            ),
            ({"reference_images": (Path("r.png"),)}, "reference images"),
            ({"ref_names": ("some-asset",)}, "reference images"),
            ({"reference_entities": ("ent-1",)}, "reference images"),
        ],
    )
    def test_pure_avatar_rejects_every_image_input(
        self, kwargs: dict[str, object], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            GenerateVideoRequest(prompt="p", mode=Mode.AVATAR, **kwargs)  # type: ignore[arg-type]

    def test_pure_avatar_rejects_a_model_with_no_references_workflow(self) -> None:
        """veo-quality has a reference cap of 0, i.e. no ingredients workflow —
        and the likeness attach needs exactly that sub-mode."""
        with pytest.raises(ValueError, match="references/ingredients workflow"):
            GenerateVideoRequest(prompt="p", mode=Mode.AVATAR, model=VideoModel.VEO_3_1_QUALITY)

    def test_pure_avatar_accepts_models_that_do_offer_references(self) -> None:
        for model in (
            VideoModel.OMNI_FLASH,
            VideoModel.VEO_3_1_LITE,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
        ):
            req = GenerateVideoRequest(prompt="p", mode=Mode.AVATAR, model=model)
            assert req.attaches_likeness is True

    def test_pure_avatar_carries_the_ordinary_video_knobs(self) -> None:
        req = GenerateVideoRequest(
            prompt="p",
            mode=Mode.AVATAR,
            aspect=Aspect.LANDSCAPE,
            model=VideoModel.OMNI_FLASH,
            duration=10,
            count=3,
        )

        assert req.aspect is Aspect.LANDSCAPE
        assert req.duration == 10
        assert req.count == 3


class TestAvatarModeSymmetry:
    def test_t2v_rejects_an_unexpected_avatar_flag(self) -> None:
        with pytest.raises(ValueError, match="T2V request must not carry use_avatar"):
            GenerateVideoRequest(prompt="p", mode=Mode.T2V, use_avatar=True)

    def test_t2v_default_is_unaffected(self) -> None:
        """Regression guard: the avatar work must not make plain t2v attach
        anything."""
        req = GenerateVideoRequest(prompt="a golden sunset")

        assert req.mode is Mode.T2V
        assert req.use_avatar is False
        assert req.attaches_likeness is False

    def test_i2v_rejects_avatar_with_a_start_frame(self) -> None:
        with pytest.raises(ValueError, match="I2V request must not carry use_avatar"):
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.I2V,
                start_image=Path("a.png"),
                use_avatar=True,
            )

    def test_i2v_without_avatar_still_works(self) -> None:
        req = GenerateVideoRequest(prompt="p", mode=Mode.I2V, start_image=Path("a.png"))

        assert req.attaches_likeness is False

    def test_r2v_accepts_reference_images_plus_avatar(self) -> None:
        req = GenerateVideoRequest(
            prompt="walking with the referenced subjects",
            mode=Mode.R2V,
            reference_images=(Path("subject.png"),),
            model=VideoModel.OMNI_FLASH,
            use_avatar=True,
        )

        assert req.mode is Mode.R2V
        assert req.attaches_likeness is True
        assert req.reference_images == (Path("subject.png"),)

    def test_r2v_avatar_still_honours_the_per_model_reference_cap(self) -> None:
        """The avatar must not become a way around the ingredient cap."""
        with pytest.raises(ValueError, match="at most 3 reference image"):
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.R2V,
                model=VideoModel.VEO_3_1_LITE,
                reference_images=tuple(Path(f"{i}.png") for i in range(4)),
                use_avatar=True,
            )

    def test_r2v_without_avatar_is_unchanged(self) -> None:
        req = GenerateVideoRequest(prompt="p", mode=Mode.R2V, reference_images=(Path("a.png"),))

        assert req.attaches_likeness is False
