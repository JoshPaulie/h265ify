from unittest.mock import MagicMock, patch
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob, _delete_user_file
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder


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
