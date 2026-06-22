"""Tests for pipeline.py - pure helpers, job preparation, replace mode."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from h265ify.pipeline import (
    ReplacePair,
    get_output_path,
    _tmp_path,
    find_replace_pairs,
    find_video_files,
    prepare_jobs,
    run_replace,
)
from h265ify.probe import ProbeResult


@pytest.fixture
def console() -> Console:
    return Console(highlight=False)


def _make_probe(
    path: str = "/tmp/video.mkv",
    is_h265: bool = False,
    file_size: int = 100_000_000,
) -> ProbeResult:
    return ProbeResult(
        path=Path(path),
        is_h265=is_h265,
        video_codec="hevc" if is_h265 else "h264",
        width=1920,
        height=1080,
        duration=60.0,
        file_size=file_size,
    )


# ---------------------------------------------------------------------------
# get_output_path
# ---------------------------------------------------------------------------
class TestOutputPath:
    def test_suffix_mode_preserves_mkv(self) -> None:
        result = get_output_path(Path("/tmp/video.mkv"), replace=False)
        assert result == Path("/tmp/video_h265.mkv")

    def test_suffix_mode_preserves_mp4(self) -> None:
        result = get_output_path(Path("/tmp/video.mp4"), replace=False)
        assert result == Path("/tmp/video_h265.mp4")

    def test_format_override_to_mp4(self) -> None:
        result = get_output_path(
            Path("/tmp/video.mkv"), replace=False, output_format="mp4"
        )
        assert result == Path("/tmp/video_h265.mp4")

    def test_format_override_to_mkv(self) -> None:
        result = get_output_path(
            Path("/tmp/video.mp4"), replace=False, output_format="mkv"
        )
        assert result == Path("/tmp/video_h265.mkv")

    def test_replace_mode_preserves_container(self) -> None:
        result = get_output_path(Path("/tmp/video.mkv"), replace=True)
        assert result == Path("/tmp/video.mkv")

    def test_replace_mode_already_mp4(self) -> None:
        result = get_output_path(Path("/tmp/video.mp4"), replace=True)
        assert result == Path("/tmp/video.mp4")

    def test_replace_mode_with_format_override(self) -> None:
        result = get_output_path(
            Path("/tmp/video.mkv"), replace=True, output_format="mp4"
        )
        assert result == Path("/tmp/video.mp4")

    def test_suffix_mode_with_dots_in_name(self) -> None:
        result = get_output_path(Path("/tmp/video.final.cut.mkv"), replace=False)
        assert result == Path("/tmp/video.final.cut_h265.mkv")


# ---------------------------------------------------------------------------
# _tmp_path
# ---------------------------------------------------------------------------
class TestTmpPath:
    def test_creates_tmp_from_output(self) -> None:
        output = Path("/tmp/video_h265.mp4")
        assert _tmp_path(output) == Path("/tmp/video_h265.h265-tmp.mp4")

    def test_creates_tmp_from_yolo_output(self) -> None:
        output = Path("/tmp/video.mp4")
        result = _tmp_path(output)
        assert result == Path("/tmp/video.h265-tmp.mp4")

    def test_tmp_mkv(self) -> None:
        output = Path("/tmp/video.mkv")
        result = _tmp_path(output)
        assert result == Path("/tmp/video.h265-tmp.mkv")


# ---------------------------------------------------------------------------
# prepare_jobs
# ---------------------------------------------------------------------------
class TestPrepareJobs:
    def test_all_h265_skipped(self) -> None:
        probes = [
            _make_probe("/tmp/a.mkv", is_h265=True),
            _make_probe("/tmp/b.mp4", is_h265=True),
        ]
        jobs, skipped = prepare_jobs(probes, replace=False)
        assert jobs == []
        assert len(skipped) == 2

    def test_non_h265_creates_jobs(self) -> None:
        probes = [
            _make_probe("/tmp/a.mkv", is_h265=False),
            _make_probe("/tmp/b.mp4", is_h265=False),
        ]
        jobs, skipped = prepare_jobs(probes, replace=False)
        assert len(jobs) == 2
        assert skipped == []

    def test_mixed(self) -> None:
        probes = [
            _make_probe("/tmp/a.mkv", is_h265=True),
            _make_probe("/tmp/b.mkv", is_h265=False),
        ]
        jobs, skipped = prepare_jobs(probes, replace=False)
        assert len(jobs) == 1
        assert jobs[0].input_path == Path("/tmp/b.mkv")
        assert len(skipped) == 1
        assert skipped[0].path == Path("/tmp/a.mkv")

    def test_replace_mode_skips_h265(self) -> None:
        probes = [
            _make_probe("/tmp/a.mkv", is_h265=True),
            _make_probe("/tmp/b.mp4", is_h265=False),
        ]
        jobs, skipped = prepare_jobs(probes, replace=True)
        assert len(skipped) == 1
        assert skipped[0].is_h265
        assert len(jobs) == 1

    def test_skip_existing(self, tmp_path: Path) -> None:
        """When skip_existing=True and output already exists, it's skipped."""
        h265_file = tmp_path / "video_h265.mkv"
        h265_file.write_text("fake output")
        src_file = tmp_path / "video.mkv"
        src_file.write_text("fake source")

        probes = [
            ProbeResult(
                path=src_file,
                is_h265=False,
                video_codec="h264",
                width=1920,
                height=1080,
                duration=60.0,
                file_size=100_000,
            )
        ]
        jobs, skipped = prepare_jobs(probes, replace=False, skip_existing=True)
        assert jobs == []
        assert len(skipped) == 1
        assert not skipped[0].is_h265

    def test_skip_existing_false(self, tmp_path: Path) -> None:
        """When skip_existing=False, existing _h265 is overwritten."""
        h265_file = tmp_path / "video_h265.mkv"
        h265_file.write_text("fake output")
        src_file = tmp_path / "video.mkv"
        src_file.write_text("fake source")

        probes = [
            ProbeResult(
                path=src_file,
                is_h265=False,
                video_codec="h264",
                width=1920,
                height=1080,
                duration=60.0,
                file_size=100_000,
            )
        ]
        jobs, skipped = prepare_jobs(probes, replace=False, skip_existing=False)
        assert len(jobs) == 1
        assert not skipped

    def test_replace_mode_ignores_existing_output(self, tmp_path: Path) -> None:
        """In yolo mode, skip_existing check is bypassed."""
        h265_file = tmp_path / "video_h265.mkv"
        h265_file.write_text("fake output")
        src_file = tmp_path / "video.mkv"
        src_file.write_text("fake source")

        probes = [
            ProbeResult(
                path=src_file,
                is_h265=False,
                video_codec="h264",
                width=1920,
                height=1080,
                duration=60.0,
                file_size=100_000,
            )
        ]
        jobs, skipped = prepare_jobs(probes, replace=True, skip_existing=True)
        assert len(jobs) == 1
        assert not skipped

    def test_empty_input(self) -> None:
        jobs, skipped = prepare_jobs([], replace=False)
        assert jobs == []
        assert skipped == []

    def test_output_format_passed_through(self, tmp_path: Path) -> None:
        """prepare_jobs uses output_format via get_output_path."""
        src_file = tmp_path / "video.mkv"
        src_file.write_text("source")
        h265_file = tmp_path / "video_h265.mp4"
        h265_file.write_text("fake output")

        probes = [
            ProbeResult(
                path=src_file,
                is_h265=False,
                video_codec="h264",
                width=1920,
                height=1080,
                duration=60.0,
                file_size=100_000,
            )
        ]
        # With output_format="mp4", the output is video_h265.mp4 which exists
        jobs, skipped = prepare_jobs(
            probes, replace=False, skip_existing=True, output_format="mp4"
        )
        assert jobs == []  # skipped because _h265.mp4 already exists
        assert len(skipped) == 1


# ---------------------------------------------------------------------------
# find_replace_pairs
# ---------------------------------------------------------------------------
class TestFindReplacePairs:
    def test_empty_paths(self, console: Console) -> None:
        pairs = find_replace_pairs([], console)
        assert pairs == []

    def test_no_h265_files(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mp4"
        src.write_text("source")
        pairs = find_replace_pairs([tmp_path], console)
        assert pairs == []

    def test_h265_with_original_mp4(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mp4"
        src.write_text("source")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 copy")

        pairs = find_replace_pairs([tmp_path], console)
        assert len(pairs) == 1
        assert pairs[0].h265_path == h265f
        assert pairs[0].original_path == src

    def test_h265_with_original_mkv(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mkv"
        src.write_text("source")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 copy")

        pairs = find_replace_pairs([tmp_path], console)
        assert len(pairs) == 1
        assert pairs[0].original_path == src
        assert pairs[0].h265_path == h265f

    def test_original_not_found_warns(self, tmp_path: Path, console: Console) -> None:
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 copy")
        # No original video.* file

        pairs = find_replace_pairs([tmp_path], console)
        assert pairs == []

    def test_multiple_pairs(self, tmp_path: Path, console: Console) -> None:
        (tmp_path / "a.mkv").write_text("orig")
        (tmp_path / "a_h265.mkv").write_text("h265")
        (tmp_path / "b.mp4").write_text("orig")
        (tmp_path / "b_h265.mp4").write_text("h265")

        pairs = find_replace_pairs([tmp_path], console)
        assert len(pairs) == 2

    def test_only_h265_ends_with_suffix(self, tmp_path: Path, console: Console) -> None:
        """Files with _h265 mid-stem are not treated as h265 copies."""
        # "my_h265_video.mp4" has _h265 in the middle - NOT a match
        # (it's not treated as an h265 copy because stem doesn't END with _h265)
        (tmp_path / "my_h265_video.mp4").write_text("original")
        # "my_video_h265.mp4" ends with _h265 - IS a match
        (tmp_path / "my_video_h265.mp4").write_text("h265 copy")
        # Original for my_video_h265.mp4
        (tmp_path / "my_video.mp4").write_text("original video")

        pairs = find_replace_pairs([tmp_path], console)
        assert len(pairs) == 1
        assert pairs[0].h265_path.stem == "my_video_h265"

    def test_accepts_file_paths(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mp4"
        src.write_text("source")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 copy")

        pairs = find_replace_pairs([h265f], console)
        assert len(pairs) == 1
        assert pairs[0].h265_path == h265f


# ---------------------------------------------------------------------------
# run_replace
# ---------------------------------------------------------------------------
class TestRunReplace:
    def test_dry_run(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mp4"
        src.write_text("source")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 copy")

        pairs = [ReplacePair(h265_path=h265f, original_path=src)]
        replaced, skipped = run_replace(pairs, console, dry_run=True)

        assert replaced == 1
        assert skipped == 0
        # Files unchanged in dry-run
        assert src.exists()
        assert h265f.exists()

    def test_actual_replace(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mkv"
        src.write_text("original video")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 content")

        pairs = [ReplacePair(h265_path=h265f, original_path=src)]
        replaced, skipped = run_replace(pairs, console, dry_run=False)

        assert replaced == 1
        assert skipped == 0
        # Original should be gone
        assert not src.exists()
        # h265 file renamed to original stem + h265 extension
        new_path = tmp_path / "video.mp4"
        assert new_path.exists()
        assert new_path.read_text() == "h265 content"
        assert not h265f.exists()

    def test_permanent_deletes_original(self, tmp_path: Path, console: Console) -> None:
        src = tmp_path / "video.mkv"
        src.write_text("original video")
        h265f = tmp_path / "video_h265.mp4"
        h265f.write_text("h265 content")

        pairs = [ReplacePair(h265_path=h265f, original_path=src)]
        replaced, skipped = run_replace(pairs, console, dry_run=False, permanent=True)

        assert replaced == 1
        assert skipped == 0
        assert not src.exists()  # permanently deleted
        new_path = tmp_path / "video.mp4"
        assert new_path.exists()
        assert new_path.read_text() == "h265 content"


# ---------------------------------------------------------------------------
# find_video_files
# ---------------------------------------------------------------------------
class TestFindVideoFiles:
    def test_finds_videos_in_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.mp4").write_bytes(b"")
        (tmp_path / "b.mkv").write_bytes(b"")
        result = find_video_files([tmp_path])
        names = {f.name for f in result}
        assert "a.mp4" in names
        assert "b.mkv" in names

    def test_skips_h265_stems(self, tmp_path: Path) -> None:
        (tmp_path / "a.mp4").write_bytes(b"")
        (tmp_path / "a_h265.mp4").write_bytes(b"")
        result = find_video_files([tmp_path])
        names = {f.name for f in result}
        assert "a.mp4" in names
        assert "a_h265.mp4" not in names

    def test_accepts_file_path_directly(self, tmp_path: Path) -> None:
        f = tmp_path / "video.mp4"
        f.write_bytes(b"")
        result = find_video_files([f])
        assert result == [f.resolve()]

    def test_skips_non_video_files(self, tmp_path: Path) -> None:
        (tmp_path / "image.jpg").write_bytes(b"")
        (tmp_path / "video.mp4").write_bytes(b"")
        result = find_video_files([tmp_path])
        names = {f.name for f in result}
        assert "video.mp4" in names
        assert "image.jpg" not in names

    def test_recursive(self, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "deep.mkv").write_bytes(b"")
        result = find_video_files([tmp_path])
        names = {f.name for f in result}
        assert "deep.mkv" in names

    def test_nonexistent_path_warns_and_returns_empty(
        self, tmp_path: Path, console: Console
    ) -> None:
        result = find_video_files([tmp_path / "nonexistent"], console=console)
        assert result == []

    def test_no_duplicates_from_repeated_path(self, tmp_path: Path) -> None:
        (tmp_path / "video.mp4").write_bytes(b"")
        result = find_video_files([tmp_path, tmp_path])  # same dir twice
        assert len(result) == 1
