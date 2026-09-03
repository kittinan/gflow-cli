"""CLI contract for `gflow image avatar`, `gflow video avatar` and `r2v --avatar`.

The interesting claims are: what request shape the command builds (that is what
reaches Flow and spends money), what it refuses at the CLI edge (exit 2, no
browser), that its JSON envelope is a single valid document, and that the
catalog row is written with the avatar operation kind on both success and
failure.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.video import Mode
from gflow_cli.cli_image import image
from gflow_cli.cli_video import video
from gflow_cli.data.models import OperationKind
from gflow_cli.errors import AvatarUnavailableError

# ---------------------------------------------------------------------------
# help / option surface
# ---------------------------------------------------------------------------


class TestAvatarCommandSurface:
    def test_video_avatar_is_registered_and_documents_the_region_gate(self) -> None:
        result = CliRunner().invoke(video, ["avatar", "--help"])

        assert result.exit_code == 0
        # The availability caveat is load-bearing: the feature is region gated
        # and the help must not imply otherwise.
        assert "region" in result.output.lower()
        assert "exit 37" in result.output

    def test_image_avatar_is_registered_and_documents_the_region_gate(self) -> None:
        result = CliRunner().invoke(image, ["avatar", "--help"])

        assert result.exit_code == 0
        assert "region" in result.output.lower()
        assert "exit 37" in result.output

    @pytest.mark.parametrize(
        "opt",
        ["--model", "--aspect", "--count", "--profile", "--project", "--project-name", "--json"],
    )
    def test_video_avatar_carries_the_shared_generation_options(self, opt: str) -> None:
        result = CliRunner().invoke(video, ["avatar", "--help"])

        assert opt in result.output

    @pytest.mark.parametrize(
        "opt",
        ["--model", "--aspect", "--out", "--profile", "--project", "--transport", "--json"],
    )
    def test_image_avatar_carries_the_shared_generation_options(self, opt: str) -> None:
        result = CliRunner().invoke(image, ["avatar", "--help"])

        assert opt in result.output

    def test_image_avatar_does_not_advertise_reference_flags(self) -> None:
        """The DTO refuses avatar + any reference kind, so offering the flags
        would offer a guaranteed error."""
        result = CliRunner().invoke(image, ["avatar", "--help"])

        assert "--reference-entity" not in result.output
        assert "--ref " not in result.output

    def test_r2v_offers_the_avatar_flag(self) -> None:
        result = CliRunner().invoke(video, ["r2v", "--help"])

        assert "--avatar" in result.output

    def test_avatar_commands_require_a_prompt(self) -> None:
        assert CliRunner().invoke(video, ["avatar"]).exit_code != 0
        assert CliRunner().invoke(image, ["avatar"]).exit_code != 0


class TestAvatarCliEdgeValidation:
    """Everything here must fail with exit 2 and a REASON, before a browser."""

    def test_video_avatar_rejects_agentic_ui_mode(self, tmp_path: Path) -> None:
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_avatar_video", new_callable=AsyncMock) as run,
        ):
            result = CliRunner().invoke(video, ["avatar", "p", "--ui-mode", "agentic"])

        assert result.exit_code == 2
        assert "agentic" in result.output
        run.assert_not_awaited()

    def test_image_avatar_rejects_agentic_ui_mode(self, tmp_path: Path) -> None:
        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_image._run_t2i", new_callable=AsyncMock) as run,
        ):
            result = CliRunner().invoke(image, ["avatar", "p", "--ui-mode", "agentic"])

        assert result.exit_code == 2
        assert "classic composer" in result.output
        run.assert_not_awaited()

    def test_video_avatar_rejects_a_model_without_a_references_workflow(
        self, tmp_path: Path
    ) -> None:
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_avatar_video", new_callable=AsyncMock) as run,
        ):
            result = CliRunner().invoke(video, ["avatar", "p", "--model", "veo-quality"])

        assert result.exit_code == 2
        assert "references/ingredients" in result.output
        run.assert_not_awaited()

    def test_video_avatar_rejects_duration_on_a_model_without_the_control(
        self, tmp_path: Path
    ) -> None:
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_avatar_video", new_callable=AsyncMock) as run,
        ):
            result = CliRunner().invoke(
                video, ["avatar", "p", "--model", "veo-lite", "--duration", "8"]
            )

        assert result.exit_code == 2
        run.assert_not_awaited()

    def test_video_avatar_accepts_a_model_that_does_offer_references(self, tmp_path: Path) -> None:
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_avatar_video", new_callable=AsyncMock) as run,
        ):
            result = CliRunner().invoke(video, ["avatar", "p", "--model", "omni-flash"])

        assert result.exit_code == 0, result.output
        run.assert_awaited_once()


# ---------------------------------------------------------------------------
# Request shape — what actually reaches Flow
# ---------------------------------------------------------------------------


class _FakeRecorder:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def record_started_video(self, **kwargs: Any) -> None:
        self.started.append(kwargs)

    def record_completed_video(self, **kwargs: Any) -> None:
        self.completed.append(kwargs)

    def record_failed_operation(self, **kwargs: Any) -> None:
        self.failed.append(kwargs)


def _stub_video_result(path: Path) -> Any:
    from gflow_cli.api.video import VideoResult, VideoStatus

    return VideoResult(
        status=VideoStatus(media_id="m1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=path,
        project_id="p1",
    )


def _run_video(
    args: list[str],
    tmp_path: Path,
    *,
    recorder: _FakeRecorder | None = None,
    generate: Any = None,
) -> tuple[Any, list[Any]]:
    """Invoke `gflow video ...` against a fake client; return (result, requests)."""
    from gflow_cli.api.client import FlowApiClient

    saved = tmp_path / "m1.mp4"
    saved.touch()
    seen: list[Any] = []
    rec = recorder or _FakeRecorder()

    async def _default_generate(*, req: Any, **kwargs: Any) -> Any:
        del kwargs
        seen.append(req)
        return _stub_video_result(saved)

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=rec),
        patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
        # return_value=False: a truthy __aexit__ SUPPRESSES the exception under
        # test, which silently turns a failure case into a pass.
        patch(
            "gflow_cli.api.client.FlowApiClient.__aexit__",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        client = MagicMock(spec=FlowApiClient)
        client.generate_video = generate or _default_generate
        enter.return_value = client
        result = CliRunner().invoke(video, args)
    return result, seen


class TestAvatarRequestShape:
    def test_video_avatar_builds_a_pure_avatar_request(self, tmp_path: Path) -> None:
        result, seen = _run_video(["avatar", "walking through Bangkok"], tmp_path)

        assert result.exit_code == 0, result.output
        (req,) = seen
        assert req.mode is Mode.AVATAR
        assert req.use_avatar is True
        assert req.attaches_likeness is True
        assert req.reference_images == ()
        assert req.start_image is None
        assert req.end_image is None

    def test_video_avatar_threads_aspect_model_and_count(self, tmp_path: Path) -> None:
        result, seen = _run_video(
            [
                "avatar",
                "p",
                "--aspect",
                "16:9",
                "--model",
                "omni-flash",
                "--duration",
                "10",
                "--count",
                "2",
            ],
            tmp_path,
        )

        assert result.exit_code == 0, result.output
        (req,) = seen
        assert req.aspect.value == "landscape"
        assert req.model.value == "omni_flash"
        assert req.duration == 10
        assert req.count == 2

    def test_r2v_avatar_flag_produces_r2v_plus_likeness(self, tmp_path: Path) -> None:
        ref = tmp_path / "subject.png"
        ref.write_bytes(b"x")

        result, seen = _run_video(
            ["r2v", "walking with the referenced subjects", "--ref", str(ref), "--avatar"],
            tmp_path,
        )

        assert result.exit_code == 0, result.output
        (req,) = seen
        assert req.mode is Mode.R2V
        assert req.use_avatar is True
        assert req.reference_images == (ref,)

    def test_r2v_without_the_flag_is_unchanged(self, tmp_path: Path) -> None:
        ref = tmp_path / "subject.png"
        ref.write_bytes(b"x")

        result, seen = _run_video(["r2v", "p", "--ref", str(ref)], tmp_path)

        assert result.exit_code == 0, result.output
        (req,) = seen
        assert req.use_avatar is False

    def test_t2v_is_unchanged_and_attaches_nothing(self, tmp_path: Path) -> None:
        result, seen = _run_video(["t2v", "a sunset"], tmp_path)

        assert result.exit_code == 0, result.output
        (req,) = seen
        assert req.mode is Mode.T2V
        assert req.attaches_likeness is False


class TestAvatarOutputAndProject:
    def test_output_flag_relocates_the_artifact(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "clip.mp4"

        result, _ = _run_video(["avatar", "p", "-o", str(target)], tmp_path)

        assert result.exit_code == 0, result.output
        assert target.exists()

    def test_explicit_project_id_is_reused_not_recreated(self, tmp_path: Path) -> None:
        from gflow_cli.api.client import FlowApiClient

        saved = tmp_path / "m1.mp4"
        saved.touch()
        seen_project: list[str | None] = []

        async def _generate(*, req: Any, project_id: str | None = None, **kwargs: Any) -> Any:
            del req, kwargs
            seen_project.append(project_id)
            return _stub_video_result(saved)

        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=_FakeRecorder()),
            patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
            # return_value=False: a truthy __aexit__ SUPPRESSES the exception under
            # test, which silently turns a failure case into a pass.
            patch(
                "gflow_cli.api.client.FlowApiClient.__aexit__",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            client = MagicMock(spec=FlowApiClient)
            client.generate_video = _generate
            enter.return_value = client
            result = CliRunner().invoke(video, ["avatar", "p", "--project", "proj-123"])

        assert result.exit_code == 0, result.output
        assert seen_project == ["proj-123"]
        # A supplied project must NOT be re-created.
        client.create_project.assert_not_called()


class TestAvatarJsonOutput:
    def test_video_avatar_json_is_one_valid_document(self, tmp_path: Path) -> None:
        result, _ = _run_video(["avatar", "a walk", "--json"], tmp_path)

        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["status"] == "ok"
        assert data["command"] == "video avatar"
        assert data["request"]["mode"] == "avatar"

    def test_avatar_unavailable_json_is_one_valid_error_document(
        self,
        tmp_path: Path,
        install_log_capture: Any,
    ) -> None:
        """The region gate must reach a programmatic caller as a single parseable
        RFC 9457-shaped envelope with the right exit code — not two documents.

        ``install_log_capture`` routes the structured ``error_raised`` event into
        a list instead of the default stdout renderer, so the assertion is about
        the JSON channel alone (the two channels are separate by design — see
        tests/test_self_documenting_errors.py).
        """
        del install_log_capture

        async def _refuse(**kwargs: Any) -> Any:
            del kwargs
            raise AvatarUnavailableError("account not eligible: REGION")

        result, _ = _run_video(["avatar", "p", "--json"], tmp_path, generate=_refuse)

        assert result.exit_code == 37, result.output
        data = _json.loads(result.output)
        assert data["status"] == "fail"
        assert data["error"]["class"] == "AvatarUnavailableError"
        assert data["error"]["exit_code"] == 37
        assert data["error"]["retryable"] is False
        assert data["error"]["remediation_hint"]


class TestAvatarOperationRecording:
    def test_successful_avatar_video_records_started_and_completed(self, tmp_path: Path) -> None:
        from gflow_cli.api.video import VideoStarted

        saved = tmp_path / "m1.mp4"
        saved.touch()
        recorder = _FakeRecorder()

        async def _generate(*, req: Any, on_started: Any = None, **kwargs: Any) -> Any:
            del kwargs
            assert req.mode is Mode.AVATAR
            if on_started is not None:
                on_started(VideoStarted(media_id="m1", project_id="p1"))
            return _stub_video_result(saved)

        result, _ = _run_video(["avatar", "p"], tmp_path, recorder=recorder, generate=_generate)

        assert result.exit_code == 0, result.output
        assert len(recorder.started) == 1
        assert len(recorder.completed) == 1
        assert recorder.completed[0]["request"].mode is Mode.AVATAR
        assert recorder.closed is True

    def test_failed_avatar_video_records_the_avatar_operation_kind(self, tmp_path: Path) -> None:
        """#341: the failure must be persisted, and under the AVATAR kind — a
        run recorded as `t2v` would make the avatar history unfindable."""
        recorder = _FakeRecorder()
        seen: list[dict[str, Any]] = []

        async def _boom(**kwargs: Any) -> Any:
            del kwargs
            raise AvatarUnavailableError("account not eligible: REGION")

        def _capture(rec: Any, **kwargs: Any) -> None:
            del rec
            seen.append(kwargs)

        with patch("gflow_cli.cli_video.record_failed_operation_safe", _capture):
            result, _ = _run_video(["avatar", "p"], tmp_path, recorder=recorder, generate=_boom)

        assert result.exit_code == 37, result.output
        assert len(seen) == 1
        assert seen[0]["mode"] is OperationKind.AVATAR
        assert seen[0]["command"] == "video avatar"


class TestImageAvatarWiring:
    def test_image_avatar_builds_a_likeness_request_with_avatar_provenance(
        self, tmp_path: Path
    ) -> None:
        """`image avatar` must reuse the t2i pipeline while relabelling the
        operation — otherwise the catalog cannot tell the two apart."""
        seen: dict[str, Any] = {}

        async def _capture(**kwargs: Any) -> None:
            seen.update(kwargs)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_image._run_t2i", _capture),
        ):
            result = CliRunner().invoke(
                image, ["avatar", "cinematic portrait", "--aspect", "1:1", "-n", "3"]
            )

        assert result.exit_code == 0, result.output
        assert seen["req"].use_avatar is True
        assert seen["req"].refs == ()
        assert seen["req"].reference_entities == ()
        assert seen["count"] == 3
        assert seen["command"] == "image avatar"
        assert seen["operation_kind"] is OperationKind.AVATAR
        assert seen["project_prefix"] == "gflow-avatar"

    def test_image_t2i_keeps_its_own_provenance_defaults(self, tmp_path: Path) -> None:
        """Regression guard for the shared pipeline: parameterising `_run_t2i`
        must not have changed what plain t2i records."""
        seen: dict[str, Any] = {}

        async def _capture(**kwargs: Any) -> None:
            seen.update(kwargs)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_image._run_t2i", _capture),
        ):
            result = CliRunner().invoke(image, ["t2i", "a forest"])

        assert result.exit_code == 0, result.output
        assert seen["req"].use_avatar is False
        # Defaults are applied by the signature, so the t2i call site stays silent.
        assert seen.get("command", "image t2i") == "image t2i"
        assert seen.get("operation_kind", OperationKind.T2I) is OperationKind.T2I
