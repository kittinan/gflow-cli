"""Pre-flight eligibility gate for Avatar/likeness generation.

Flow's Avatar is verified-identity AND region gated. This gate is the cheapest
place to stop an avatar request — a free Bearer GET, before any reCAPTCHA mint
or credit-spending submit. Its hardest requirement is the THIRD state: an
inconclusive probe must neither refuse a working account nor wave through a
generation that will silently drop the likeness.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api import routes
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import LikenessEligibility
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, Mode
from gflow_cli.errors import AuthExpiredError, AvatarUnavailableError, NetworkError


def _client_with_get(return_value: Any = None, side_effect: Any = None) -> tuple[Any, AsyncMock]:
    client = FlowApiClient.__new__(FlowApiClient)
    client._page = None  # type: ignore[assignment]
    client._page_queue = None
    client._context = None
    client._access_token = "ya29.test"
    client._access_token_exp = 9_999_999_999
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    client._get_json = mock  # type: ignore[method-assign]
    return client, mock


class TestEligibilityParsing:
    def test_empty_reasons_means_eligible(self) -> None:
        result = LikenessEligibility.from_response({"ineligibilityReasons": []})

        assert (result.eligible, result.determined) == (True, True)

    def test_absent_reasons_key_means_eligible(self) -> None:
        """Flow omits the field when there is nothing to report."""
        result = LikenessEligibility.from_response({})

        assert (result.eligible, result.determined) == (True, True)

    def test_null_reasons_means_eligible(self) -> None:
        result = LikenessEligibility.from_response({"ineligibilityReasons": None})

        assert (result.eligible, result.determined) == (True, True)

    def test_region_is_a_determined_no(self) -> None:
        result = LikenessEligibility.from_response({"ineligibilityReasons": ["REGION"]})

        assert result.determined is True
        assert result.eligible is False
        assert result.reasons == ("REGION",)

    @pytest.mark.parametrize("body", ["not-a-dict", 42, None, ["REGION"]])
    def test_an_unrecognised_body_is_undetermined_not_a_guess(self, body: Any) -> None:
        result = LikenessEligibility.from_response(body)

        assert result.determined is False
        # An undetermined answer must never read as a green light.
        assert result.eligible is False

    def test_a_non_list_reasons_field_is_undetermined(self) -> None:
        result = LikenessEligibility.from_response({"ineligibilityReasons": "REGION"})

        assert result.determined is False


class TestCheckLikenessEligibility:
    @pytest.mark.asyncio
    async def test_calls_the_free_bearer_route(self) -> None:
        client, mock = _client_with_get({"ineligibilityReasons": []})

        result = await client.check_likeness_eligibility()

        mock.assert_awaited_once()
        assert mock.call_args.args[0] == routes.LIKENESS_CHECK_ELIGIBILITY
        assert result.eligible is True

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_undetermined_not_an_error(self) -> None:
        """A wire hiccup must not refuse a working account."""
        client, _ = _client_with_get(side_effect=NetworkError("HTTP 503"))

        result = await client.check_likeness_eligibility()

        assert result.determined is False

    @pytest.mark.asyncio
    async def test_an_expired_session_still_surfaces(self) -> None:
        """Auth is actionable and non-avatar-specific; swallowing it would hide
        the real problem behind a confusing avatar message later."""
        client, _ = _client_with_get(side_effect=AuthExpiredError("expired"))

        with pytest.raises(AuthExpiredError):
            await client.check_likeness_eligibility()


class TestEligibilityGatesGeneration:
    @pytest.mark.asyncio
    async def test_a_determined_no_refuses_before_any_generation(self) -> None:
        client, _ = _client_with_get({"ineligibilityReasons": ["REGION"]})

        with pytest.raises(AvatarUnavailableError) as excinfo:
            await client._require_likeness_eligibility(surface="video")

        assert "REGION" in excinfo.value.detail
        assert "no credits were spent" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_a_determined_yes_passes(self) -> None:
        client, _ = _client_with_get({"ineligibilityReasons": []})

        assert await client._require_likeness_eligibility(surface="image") is None

    @pytest.mark.asyncio
    async def test_undetermined_defers_to_the_ui_gate(self) -> None:
        """Two gates: an inconclusive REST probe hands off to the media-dialog
        inspection, which still refuses to submit if the Avatar tab is absent."""
        client, _ = _client_with_get(side_effect=NetworkError("boom"))

        assert await client._require_likeness_eligibility(surface="video") is None

    @pytest.mark.asyncio
    async def test_video_generation_checks_eligibility_before_the_transport(self) -> None:
        """The refusal must happen with the transport untouched — that is what
        makes it a zero-spend abort."""
        client, _ = _client_with_get({"ineligibilityReasons": ["REGION"]})
        transport = AsyncMock()
        transport.generate_video = AsyncMock(
            side_effect=AssertionError("transport must not run after an ineligible verdict")
        )
        client.transport = transport

        with pytest.raises(AvatarUnavailableError):
            await client.generate_video(
                req=GenerateVideoRequest(prompt="p", mode=Mode.AVATAR),
                download=False,
            )
        transport.generate_video.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_non_avatar_video_never_pays_for_the_probe(self) -> None:
        """Regression guard: the gate must be invisible to t2v/i2v/r2v."""
        client, get_json = _client_with_get({"ineligibilityReasons": ["REGION"]})
        transport = AsyncMock()
        transport.generate_video = AsyncMock(return_value="ok")
        client.transport = transport

        await client.generate_video(req=GenerateVideoRequest(prompt="p"), download=False)

        get_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_generation_refuses_before_minting_a_recaptcha_token(self) -> None:
        """An ineligible account must not burn a token (nor the WAF heat that
        minting one costs) on a request that can never succeed."""
        client, _ = _client_with_get({"ineligibilityReasons": ["REGION"]})
        client.transport = AsyncMock()
        mint = AsyncMock(side_effect=AssertionError("must not mint for an ineligible account"))
        client._mint_recaptcha_token = mint  # type: ignore[method-assign]

        with pytest.raises(AvatarUnavailableError):
            await client._drive_images_generation(
                project_id="p1",
                req=GenerateImageRequest(prompt="p", use_avatar=True),
                recaptcha_action="imageGeneration",
            )

        mint.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_non_avatar_image_never_pays_for_the_probe(self) -> None:
        client, get_json = _client_with_get({"ineligibilityReasons": ["REGION"]})
        transport = AsyncMock()
        transport.generate_images = AsyncMock(return_value=[object()])
        client.transport = transport
        client._mint_recaptcha_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]

        await client._drive_images_generation(
            project_id="p1",
            req=GenerateImageRequest(prompt="p"),
            recaptcha_action="imageGeneration",
        )

        get_json.assert_not_awaited()
