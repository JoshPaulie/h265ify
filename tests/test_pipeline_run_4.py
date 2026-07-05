from pathlib import Path
from unittest.mock import MagicMock, patch

from h265ify.hardware import Encoder
from h265ify.pipeline import EncodeJob, run_pipeline
from h265ify.probe import ProbeResult


def test_run_pipeline_success_file_size_zero() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"), ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 0)
    )  # file_size = 0
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch(
                    "h265ify.pipeline.os.replace"
                ):  # Added to avoid OSError making success=False
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1
                    assert results[0].success


def test_run_pipeline_failure_cleanup_tmp() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"), ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 0, 1000)
    )  # duration = 0
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(False, [])):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.unlink") as mock_unlink:
                results, interrupted = run_pipeline(
                    [job], enc, 23, False, False, console
                )
                assert not interrupted
                assert len(results) == 1
                assert not results[0].success
                mock_unlink.assert_called_with(missing_ok=True)


def test_run_pipeline_on_job_complete() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")
    callback = MagicMock()

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("h265ify.pipeline.os.replace"):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console, on_job_complete=callback
                    )
                    assert not interrupted
                    callback.assert_called_once()


def test_run_pipeline_multiple_failure_stop() -> None:
    console = MagicMock()
    job1 = EncodeJob(
        Path("in1.mp4"),
        ProbeResult(Path("in1.mp4"), False, "h264", 1920, 1080, 0, 1000),
    )  # duration 0
    job2 = EncodeJob(
        Path("in2.mp4"),
        ProbeResult(Path("in2.mp4"), False, "h264", 1920, 1080, 0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(False, [])):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.unlink"):
                with patch("h265ify.pipeline.Progress"):
                    results, interrupted = run_pipeline(
                        [job1, job2], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1  # Stops on first failure


def test_run_pipeline_keyboard_interrupt() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", side_effect=KeyboardInterrupt):
        results, interrupted = run_pipeline([job], enc, 23, False, False, console)
        assert interrupted
        assert len(results) == 0


def test_run_pipeline_retry_on_crash_then_succeeds() -> None:
    """Crash twice, succeed on the third attempt."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    # First 2 calls fail, 3rd succeeds
    encode_returns = [(False, []), (False, []), (True, [])]

    with patch(
        "h265ify.pipeline.run_encode", side_effect=encode_returns
    ) as mock_encode:
        with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.unlink"):
                    with patch("h265ify.pipeline.os.replace"):
                        results, interrupted = run_pipeline(
                            [job], enc, 23, False, False, console
                        )
                        assert not interrupted
                        assert len(results) == 1
                        assert results[0].success
                        # 3 total attempts (1 initial + 2 retries)
                        assert mock_encode.call_count == 3


def test_run_pipeline_retry_exhausted_fails() -> None:
    """All 3 attempts fail — final result is failure."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch(
        "h265ify.pipeline.run_encode", return_value=(False, [])
    ) as mock_encode:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.unlink"):
                results, interrupted = run_pipeline(
                    [job], enc, 23, False, False, console
                )
                assert not interrupted
                assert len(results) == 1
                assert not results[0].success
                # 3 total attempts (1 initial + 2 retries)
                assert mock_encode.call_count == 3


def test_run_pipeline_retry_not_triggered_by_success() -> None:
    """A first-attempt success calls run_encode exactly once."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch(
        "h265ify.pipeline.run_encode", return_value=(True, [])
    ) as mock_encode:
        with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("h265ify.pipeline.os.replace"):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1
                    assert results[0].success
                    assert mock_encode.call_count == 1
