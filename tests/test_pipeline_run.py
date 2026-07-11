from typing import Any
from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder


def test_run_pipeline_dry_run_extra() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    results, interrupted = run_pipeline(
        [job],
        enc,
        23,
        False,
        True,
        console,
        resize="720p",
        no_upscale=True,
        reencode_audio=True,
    )
    assert not interrupted
    assert len(results) == 1
    console.print.assert_any_call(
        "  in.mp4 (1000.0 B)"
    )


def test_sigint_handler() -> None:
    # just invoke it
    from h265ify.pipeline import run_pipeline

    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    def mock_run_encode(*args: Any, **kwargs: Any) -> tuple[bool, list[str]]:
        raise KeyboardInterrupt()

    with patch("h265ify.pipeline.run_encode", side_effect=mock_run_encode):
        results, interrupted = run_pipeline([job], enc, 23, False, False, console)
        assert interrupted is True
