from typing import Any
from unittest.mock import patch, MagicMock
from pathlib import Path
from h265ify.pipeline import run_pipeline, EncodeJob
from h265ify.probe import ProbeResult
from h265ify.hardware import Encoder


def test_pipeline_progress_speed_zero() -> None:
    console = MagicMock()
    job = EncodeJob(
        Path("in.mp4"),
        ProbeResult(Path("in.mp4"), False, "h264", 1920, 1080, 10.0, 1000),
    )
    enc = Encoder(name="libx265", is_hardware=False, label="CPU")

    with patch("h265ify.pipeline.run_encode") as mock_run_encode:

        def mock_run(
            cmd: list[str],
            duration: float,
            progress_callback: Any = None,
            **kwargs: Any,
        ) -> tuple[bool, list[str]]:
            # speed=0
            progress_callback(10.0, 0.0, 1.0)
            return True, []

        mock_run_encode.side_effect = mock_run

        with patch("h265ify.pipeline.Progress") as MockProgress:
            mock_progress = MagicMock()
            MockProgress.return_value.__enter__.return_value = mock_progress
            with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("h265ify.pipeline.os.replace"):
                        results, interrupted = run_pipeline(
                            [job], enc, 23, False, False, console
                        )
                        assert results[0].success


def test_pipeline_progress_remove_task() -> None:
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

    with patch("h265ify.pipeline.run_encode", return_value=(True, [])):
        with patch("h265ify.pipeline.Progress") as MockProgress:
            mock_progress = MagicMock()
            mock_progress.add_task.return_value = "some_task_id"
            MockProgress.return_value.__enter__.return_value = mock_progress
            with patch("h265ify.pipeline.os.replace"):
                with patch("pathlib.Path.stat", return_value=MagicMock(st_size=500)):
                    with patch("pathlib.Path.exists", return_value=True):
                        results, interrupted = run_pipeline(
                            [job1, job2], enc, 23, False, False, console
                        )
                        assert len(results) == 2
                        assert mock_progress.remove_task.call_count > 0
