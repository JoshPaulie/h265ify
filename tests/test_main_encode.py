from typing import Any
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from h265ify import _cmd_encode
from h265ify.probe import ProbeResult


def test_cmd_encode_no_probe_results() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        with patch("h265ify.probe_files", return_value=[]):
            with pytest.raises(SystemExit) as e:
                _cmd_encode(args, console)
            assert e.value.code == 0
            console.print.assert_any_call(
                "[yellow]no valid video files found after probing.[/]"
            )


def test_cmd_encode_no_jobs_skipped() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), True, "hevc", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            with patch("h265ify.prepare_jobs", return_value=([], [probe_res])):
                with pytest.raises(SystemExit) as e:
                    _cmd_encode(args, console)
                assert e.value.code == 0
                console.print.assert_any_call(
                    "nothing to do (all files are already h265)."
                )


def test_cmd_encode_no_jobs_skipped_not_h265() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            with patch("h265ify.prepare_jobs", return_value=([], [probe_res])):
                with pytest.raises(SystemExit) as e:
                    _cmd_encode(args, console)
                assert e.value.code == 0
                console.print.assert_any_call(
                    "nothing to do (all output files already exist)."
                )


def test_cmd_encode_success() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune="animation",
        resize="720p",
        yolo=True,
        dry_run=False,
        permanent=True,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    console.input.return_value = "yes"
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            from h265ify.pipeline import EncodeJob, EncodeResult

            job = EncodeJob(Path("test.mp4"), probe_res)
            with patch("h265ify.prepare_jobs", return_value=([job], [])):
                with patch(
                    "h265ify.run_pipeline",
                    return_value=(
                        [
                            EncodeResult(
                                Path("test.mp4"),
                                Path("test_h265.mp4"),
                                True,
                                5.0,
                                1000,
                                500,
                            )
                        ],
                        False,
                    ),
                ):
                    with patch("h265ify.print_summary"):
                        _cmd_encode(args, console)


def test_cmd_encode_success_with_skipped() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch(
        "h265ify.find_video_files", return_value=[Path("test.mp4"), Path("test2.mp4")]
    ):
        probe_res1 = ProbeResult(
            Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000
        )
        probe_res2 = ProbeResult(
            Path("test2.mp4"), True, "hevc", 1920, 1080, 10.0, 1000
        )
        with patch("h265ify.probe_files", return_value=[probe_res1, probe_res2]):
            from h265ify.pipeline import EncodeJob, EncodeResult

            job = EncodeJob(Path("test.mp4"), probe_res1)
            with patch("h265ify.prepare_jobs", return_value=([job], [probe_res2])):
                with patch(
                    "h265ify.run_pipeline",
                    return_value=(
                        [
                            EncodeResult(
                                Path("test.mp4"),
                                Path("test_h265.mp4"),
                                True,
                                5.0,
                                1000,
                                500,
                            )
                        ],
                        False,
                    ),
                ):
                    with patch("h265ify.print_summary"):
                        _cmd_encode(args, console)


def test_cmd_encode_yolo_permanent_abort() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=True,
        dry_run=False,
        permanent=True,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    console.input.return_value = "no"
    with pytest.raises(SystemExit) as e:
        _cmd_encode(args, console)
    assert e.value.code == 0


def test_cmd_encode_yolo_no_permanent() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=True,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[]):
        with pytest.raises(SystemExit) as e:
            _cmd_encode(args, console)
        assert e.value.code == 0
        console.print.assert_any_call(
            "[yellow]\u26a0  --yolo:[/] originals will be moved to trash after encoding"
        )


def test_cmd_encode_job_complete_callback() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )

    # We want to capture _on_job_complete inside _cmd_encode and invoke it
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            from h265ify.pipeline import EncodeJob, EncodeResult

            job = EncodeJob(
                Path(
                    "test_long_name_to_truncate_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp4"
                ),
                probe_res,
            )

            def mock_run_pipeline(*pargs: Any, **kwargs: Any) -> tuple[list[Any], bool]:
                on_job_complete = kwargs.get("on_job_complete")

                # Success, shrink, elapsed < 60
                res1 = EncodeResult(
                    job.input_path, Path("out.mp4"), True, 45.0, 1000, 500
                )
                if callable(on_job_complete):
                    on_job_complete(job, res1)

                # Success, grow, elapsed >= 60
                res2 = EncodeResult(
                    job.input_path, Path("out.mp4"), True, 120.0, 1000, 1500
                )
                if callable(on_job_complete):
                    on_job_complete(job, res2)

                # Success, same
                res3 = EncodeResult(
                    job.input_path, Path("out.mp4"), True, 120.0, 1000, 1000
                )
                if callable(on_job_complete):
                    on_job_complete(job, res3)

                # Success, size=0
                res4 = EncodeResult(
                    job.input_path, Path("out.mp4"), True, 120.0, 1000, 0
                )
                if callable(on_job_complete):
                    on_job_complete(job, res4)

                # Failure
                res5 = EncodeResult(
                    job.input_path, Path("out.mp4"), False, 120.0, 1000, 0
                )
                if callable(on_job_complete):
                    on_job_complete(job, res5)

                return [res1, res2, res3, res4, res5], False

            with patch("h265ify.prepare_jobs", return_value=([job], [])):
                with patch("h265ify.run_pipeline", side_effect=mock_run_pipeline):
                    with patch("h265ify.print_summary"):
                        try:
                            _cmd_encode(args, console)
                        except SystemExit as e:
                            assert e.code == 1
                        assert console.print.call_count > 5


def test_cmd_encode_fallback_cpu_warning() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=False,
        crf=23,
        preset="medium",
        tune="animation",
        resize="720p",
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[]):
        from h265ify.hardware import Encoder

        with patch(
            "h265ify.detect_encoder", return_value=Encoder("libx265", False, "CPU")
        ):
            with pytest.raises(SystemExit) as e:
                _cmd_encode(args, console)
            assert e.value.code == 0


def test_cmd_encode_hardware_tune_warning() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=False,
        crf=23,
        preset="medium",
        tune="animation",
        resize="720p",
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[]):
        from h265ify.hardware import Encoder

        with patch(
            "h265ify.detect_encoder",
            return_value=Encoder("hevc_videotoolbox", True, "VT"),
        ):
            with pytest.raises(SystemExit) as e:
                _cmd_encode(args, console)
            assert e.value.code == 0
            console.print.assert_any_call(
                "  [yellow]note:[/] --tune is ignored by VT (libx265 only)"
            )


def test_cmd_encode_nothing_to_do() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            with patch("h265ify.prepare_jobs", return_value=([], [])):
                with pytest.raises(SystemExit) as e:
                    _cmd_encode(args, console)
                assert e.value.code == 0
                console.print.assert_any_call("nothing to do.")


def test_cmd_encode_success_with_skipped_not_h265() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch(
        "h265ify.find_video_files", return_value=[Path("test.mp4"), Path("test2.mp4")]
    ):
        probe_res1 = ProbeResult(
            Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000
        )
        probe_res2 = ProbeResult(
            Path("test2.mp4"), False, "h264", 1920, 1080, 10.0, 1000
        )
        with patch("h265ify.probe_files", return_value=[probe_res1, probe_res2]):
            from h265ify.pipeline import EncodeJob, EncodeResult

            job = EncodeJob(Path("test.mp4"), probe_res1)
            with patch("h265ify.prepare_jobs", return_value=([job], [probe_res2])):
                with patch(
                    "h265ify.run_pipeline",
                    return_value=(
                        [
                            EncodeResult(
                                Path("test.mp4"),
                                Path("test_h265.mp4"),
                                True,
                                5.0,
                                1000,
                                500,
                            )
                        ],
                        False,
                    ),
                ):
                    with patch("h265ify.print_summary"):
                        _cmd_encode(args, console)
                        console.print.assert_any_call(
                            "  skip  test2.mp4  (test2_h265.mp4 exists)"
                        )


def test_cmd_encode_interrupted() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            from h265ify.pipeline import EncodeJob

            job = EncodeJob(Path("test.mp4"), probe_res)
            with patch("h265ify.prepare_jobs", return_value=([job], [])):
                with patch("h265ify.run_pipeline", return_value=([], True)):
                    with patch("h265ify.print_summary"):
                        with pytest.raises(SystemExit) as e:
                            _cmd_encode(args, console)
                        assert e.value.code == 130


def test_cmd_encode_failure() -> None:
    console = MagicMock()
    console.get_time = lambda: 1.0
    console.width = 80
    console.get_time = lambda: 1.0
    console.width = 80
    args = MagicMock(
        cpu=True,
        crf=23,
        preset="medium",
        tune=None,
        resize=None,
        yolo=False,
        dry_run=False,
        permanent=False,
        paths=[Path(".")],
        output_format=None,
        reencode_audio=False,
    )
    with patch("h265ify.find_video_files", return_value=[Path("test.mp4")]):
        probe_res = ProbeResult(Path("test.mp4"), False, "h264", 1920, 1080, 10.0, 1000)
        with patch("h265ify.probe_files", return_value=[probe_res]):
            from h265ify.pipeline import EncodeJob, EncodeResult

            job = EncodeJob(Path("test.mp4"), probe_res)
            with patch("h265ify.prepare_jobs", return_value=([job], [])):
                with patch(
                    "h265ify.run_pipeline",
                    return_value=(
                        [
                            EncodeResult(
                                Path("test.mp4"), Path("out.mp4"), False, 0.0, 0, 0
                            )
                        ],
                        False,
                    ),
                ):
                    with patch("h265ify.print_summary"):
                        with pytest.raises(SystemExit) as e:
                            _cmd_encode(args, console)
                        assert e.value.code == 1
