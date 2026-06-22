from typing import Any
from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder


def test_run_pipeline_replace_error() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch(
            "h265ify.pipeline.os.replace", side_effect=OSError("test replace error")
        ):
            results, interrupted = run_pipeline([job], enc, 23, False, False, console)
            assert not interrupted
            assert len(results) == 1
            assert not results[0].success
            console.print.assert_any_call(
                "  [red]error:[/] could not replace in.mp4: test replace error"
            )


def test_run_pipeline_with_warnings_and_errors() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    def mock_build_cmd(*args: Any, **kwargs: Any) -> list[str]:
        warnings = kwargs.get("warnings")
        if warnings is not None:
            warnings.append("test warning")
        return ["ffmpeg"]

    with patch("h265ify.pipeline.build_command", side_effect=mock_build_cmd):
        with patch("h265ify.pipeline.run_encode", return_value=(False, ["test error"])):
            with patch("pathlib.Path.unlink"):
                with patch("pathlib.Path.exists", return_value=True):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    console.print.assert_any_call("test warning")
                    console.print.assert_any_call("test error")
