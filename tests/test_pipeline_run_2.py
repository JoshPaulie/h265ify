from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob, _delete_user_file
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder


def test_run_pipeline_skip_on_increase() -> None:
    """When output is larger than input, the file should be skipped."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    # Encode succeeds, temp file exists but is LARGER than input.
    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 1500  # larger than 1000 input
            with patch("pathlib.Path.unlink") as mock_unlink:
                with patch("pathlib.Path.exists", return_value=True):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1
                    assert results[0].success  # encode itself succeeded
                    assert results[0].skipped
                    assert results[0].output_size == 1500
                    # The temp file should have been cleaned up
                    mock_unlink.assert_called()


def test_run_pipeline_skip_on_increase_same_size_keeps() -> None:
    """When output equals input in size, the file should be kept (not skipped)."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 1000  # same as input
            with patch("h265ify.pipeline.os.replace"):
                with patch("pathlib.Path.exists", return_value=True):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1
                    assert results[0].success
                    assert not results[0].skipped
                    assert results[0].output_size == 1000


def test_run_pipeline_skip_on_increase_unknown_input_size() -> None:
    """When input file_size is 0 (unknown), skip the size comparison."""
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 0),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 1500  # larger, but input_size is 0
            with patch("h265ify.pipeline.os.replace"):
                with patch("pathlib.Path.exists", return_value=True):
                    results, interrupted = run_pipeline(
                        [job], enc, 23, False, False, console
                    )
                    assert not interrupted
                    assert len(results) == 1
                    assert results[0].success
                    assert not results[0].skipped  # no comparison when size unknown


def test_delete_user_file_permanent() -> None:
    path = MagicMock()
    _delete_user_file(path, permanent=True)
    path.unlink.assert_called_with(missing_ok=True)


def test_delete_user_file_trash() -> None:
    path = MagicMock()
    with patch("h265ify.pipeline.send2trash") as mock_trash:
        _delete_user_file(path, permanent=False)
        mock_trash.assert_called_with(str(path))


def test_run_pipeline_success_replace() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("h265ify.pipeline.os.replace"):
            with patch("h265ify.pipeline._delete_user_file"):
                with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
                    with patch("pathlib.Path.exists", return_value=True):
                        # Use replace=True (yolo mode)
                        results, interrupted = run_pipeline(
                            [job], enc, 23, True, False, console
                        )
                        assert not interrupted
                        assert len(results) == 1
                        assert results[0].success
                        assert results[0].output_size == 500


def test_run_pipeline_failure() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode", return_value=(False, ["error log"])):
        with patch("pathlib.Path.unlink") as mock_unlink:
            with patch("pathlib.Path.exists", return_value=True):
                results, interrupted = run_pipeline(
                    [job], enc, 23, False, False, console
                )
                assert not interrupted
                assert len(results) == 1
                assert not results[0].success
                mock_unlink.assert_called_with(missing_ok=True)


def test_run_pipeline_halt_on_increase() -> None:
    """With halt_on_increase the batch stops after the first size-increase skip."""
    console = MagicMock()
    job1 = EncodeJob(
        Path("in1.mp4"),
        ProbeResult(Path("in1.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    job2 = EncodeJob(
        Path("in2.mp4"),
        ProbeResult(Path("in2.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    # First encode succeeds but output is larger.  Halt should prevent job2.
    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value.st_size = 1500  # larger than 1000
            with patch("pathlib.Path.unlink"):
                with patch("pathlib.Path.exists", return_value=True):
                    results, interrupted = run_pipeline(
                        [job1, job2],
                        enc,
                        23,
                        False,
                        False,
                        console,
                        halt_on_increase=True,
                    )
                    assert not interrupted
                    assert len(results) == 1  # only job1 processed
                    assert results[0].skipped
                    console.print.assert_any_call(
                        "\n  [yellow]halting batch[/] — output grew larger than input"
                    )
