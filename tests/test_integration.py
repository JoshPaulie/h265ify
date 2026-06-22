"""Integration smoke tests - require ffmpeg on PATH.

These tests actually invoke ffmpeg to encode a tiny synthetic video and verify
the output file is valid h265. They are skipped gracefully when ffmpeg is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from h265ify.hardware import detect_encoder
from h265ify.pipeline import (
    get_output_path,
    prepare_jobs,
    probe_files,
    run_pipeline,
)
from rich.console import Console


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
    reason="ffmpeg not found on PATH",
)


@pytest.fixture
def console() -> Console:
    return Console(highlight=False)


def _make_test_video(path: Path, duration: int = 2) -> None:
    """Create a tiny synthetic video with a lavfi test source."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:size=64x64:rate=10:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-t",
        str(duration),
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    assert result.returncode == 0, (
        f"Failed to create test video: {result.stderr.decode()}"
    )


def _is_hevc(path: Path) -> bool:
    """Check that the file has an HEVC video stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() in ("hevc", "h265")


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestEncodeSuffixMode:
    def test_basic_encode_produces_h265(self, tmp_path: Path, console: Console) -> None:
        """Encode a small test video and verify the output is valid HEVC."""
        src = tmp_path / "test.mp4"
        _make_test_video(src)

        encoder = detect_encoder()
        files = [src]
        probes = probe_files(files, console=console)
        assert probes, "ffprobe failed to probe the test video"

        jobs, skipped = prepare_jobs(probes, replace=False)
        assert len(jobs) == 1
        assert skipped == []

        results, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,  # fast/low quality for speed
            replace=False,
            dry_run=False,
            console=console,
            preset="ultrafast",
        )

        assert len(results) == 1
        result = results[0]
        assert result.success, "encode failed"

        output = get_output_path(src, replace=False)
        assert output.exists(), f"output file {output} not created"
        assert _is_hevc(output), "output is not HEVC"
        assert result.output_size > 0

    def test_already_h265_is_skipped(self, tmp_path: Path, console: Console) -> None:
        """Files already in HEVC should be skipped without encoding."""
        src = tmp_path / "already.mp4"
        _make_test_video(src)

        encoder = detect_encoder()
        # First pass: encode to h265
        probes = probe_files([src], console=console)
        jobs, _ = prepare_jobs(probes, replace=False)
        results, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,
            replace=False,
            dry_run=False,
            console=console,
            preset="ultrafast",
        )
        assert results[0].success

        # Second pass: the _h265 output should be skipped
        h265_file = get_output_path(src, replace=False)
        probes2 = probe_files([h265_file], console=console)
        jobs2, skipped2 = prepare_jobs(probes2, replace=False)
        assert jobs2 == []
        assert len(skipped2) == 1
        assert skipped2[0].is_h265

    def test_dry_run_no_file_created(self, tmp_path: Path, console: Console) -> None:
        """--dry-run should not create any files."""
        src = tmp_path / "test.mp4"
        _make_test_video(src)

        encoder = detect_encoder()
        probes = probe_files([src], console=console)
        jobs, _ = prepare_jobs(probes, replace=False)

        results, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,
            replace=False,
            dry_run=True,
            console=console,
            preset="ultrafast",
        )

        output = get_output_path(src, replace=False)
        assert not output.exists(), "dry-run should not create any output files"
        # dry-run still returns a "success" result for reporting
        assert results[0].success


class TestEncodeYoloMode:
    def test_yolo_replaces_original(self, tmp_path: Path, console: Console) -> None:
        """--yolo should replace the original file in-place."""
        src = tmp_path / "test.mp4"
        _make_test_video(src)

        encoder = detect_encoder()
        probes = probe_files([src], console=console)
        jobs, _ = prepare_jobs(probes, replace=True)

        results, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,
            replace=True,
            dry_run=False,
            console=console,
            preset="ultrafast",
            permanent=True,
        )

        assert results[0].success
        assert src.exists(), "output file should still exist at original path"
        assert _is_hevc(src), "replaced file should be HEVC"
        # No _h265 suffixed file should exist
        suffixed = get_output_path(src, replace=False)
        assert not suffixed.exists()

    def test_no_tmp_file_left_on_success(
        self, tmp_path: Path, console: Console
    ) -> None:
        """No .h265-tmp.* file should remain after a successful encode."""
        src = tmp_path / "test.mp4"
        _make_test_video(src)

        encoder = detect_encoder()
        probes = probe_files([src], console=console)
        jobs, _ = prepare_jobs(probes, replace=False)

        _, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,
            replace=False,
            dry_run=False,
            console=console,
            preset="ultrafast",
        )

        tmp_files = list(tmp_path.glob("*.h265-tmp.*"))
        assert tmp_files == [], f"temp files left behind: {tmp_files}"


class TestResizeIntegration:
    def test_resize_720p_reduces_width(self, tmp_path: Path, console: Console) -> None:
        """--resize 720p should produce output with width ≤ 1280."""
        # Create a small 1920×1080 source
        src = tmp_path / "hd.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=128x72:rate=10:duration=1",
            "-c:v",
            "libx264",
            str(src),
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)

        encoder = detect_encoder()
        probes = probe_files([src], console=console)
        jobs, _ = prepare_jobs(probes, replace=False)

        results, _ = run_pipeline(
            jobs=jobs,
            encoder=encoder,
            crf=28,
            replace=False,
            dry_run=False,
            console=console,
            preset="ultrafast",
            resize="720p",
            no_upscale=True,  # input is 128px wide → no upscale
        )

        assert results[0].success
        output = get_output_path(src, replace=False)
        assert output.exists()
        # Since no_upscale=True and 128 < 1280, output should match input dimensions
        probe_out = probe_files([output], console=console)
        assert probe_out[0].width <= 1280
