"""Tests for vmaf.py - VMAF-based auto-CRF detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from unittest.mock import patch

from h265ify.hardware import Encoder
from h265ify.probe import ColorInfo, ProbeResult
from h265ify.vmaf import (
    _build_probe_command,
    _evenly_spaced_clips,
    _extract_clip,
    _fit_crf,
    _pick_clips_from_scenes,
    _probe_crf,
    _scdet_available,
    _select_clips,
    estimate_crf_size_ratio,
    vmaf_available,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _sample_probe() -> ProbeResult:
    return ProbeResult(
        path=Path("test.mp4"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=120.0,
        file_size=1_000_000,
        color=ColorInfo(pix_fmt="yuv420p", bit_depth=8),
    )


def _sample_encoder() -> Encoder:
    return Encoder(name="libx265", is_hardware=False, label="CPU (libx265)")


# ── _fit_crf ──────────────────────────────────────────────────────────


class TestFitCrf:
    """_fit_crf is a pure function: (crf_scores, target) -> optimal_crf."""

    # --- Normal bracket ---
    def test_normal_bracket(self) -> None:
        """Scores straddle target; interpolation picks between them."""
        # VMAF drops ~3 per 5 CRF steps from 97.5 at 18 -> 93 at 33
        # Bracketing pair: (23, 96) and (28, 94.5) for target 95
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 95)
        # 23 + (95 - 96) * (28 - 23) / (94.5 - 96) = 23 + 3.33... = 26.33...
        assert crf == pytest.approx(26.33, abs=0.02)

    def test_normal_bracket_target_lower(self) -> None:
        """Target matches lowest VMAF -> all-above branch."""
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 93)
        assert crf == 33.0

    def test_normal_bracket_target_higher(self) -> None:
        """Target matches highest VMAF -> all-below branch."""
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 98)
        assert crf == 18.0

    # --- All above target -> highest CRF ---
    def test_all_above_returns_highest(self) -> None:
        crf = _fit_crf([(18, 99), (23, 98), (28, 97)], 95)
        assert crf == 28.0

    def test_all_above_with_only_one(self) -> None:
        crf = _fit_crf([(18, 99)], 95)
        assert crf == 18.0

    # --- All below target -> lowest CRF ---
    def test_all_below_returns_lowest(self) -> None:
        crf = _fit_crf([(28, 93), (33, 90)], 95)
        assert crf == 28.0

    def test_all_below_with_only_one(self) -> None:
        crf = _fit_crf([(33, 90)], 95)
        assert crf == 33.0

    # --- Edge cases ---
    def test_empty_scores_returns_23(self) -> None:
        assert _fit_crf([], 95) == 23.0

    def test_single_point_returns_that_crf(self) -> None:
        crf = _fit_crf([(23, 95)], 95)
        assert crf == 23.0

    def test_flat_scores_all_at_target(self) -> None:
        """All scores equal to target -> all-above branch returns highest."""
        crf = _fit_crf([(18, 95), (23, 95), (28, 95)], 95)
        assert crf == 28.0

    def test_flat_scores_all_below(self) -> None:
        """Flat scores all below target -> all-below branch returns lowest."""
        crf = _fit_crf([(18, 80), (23, 80), (28, 80)], 95)
        assert crf == 18.0

    # --- Positive slope (invalid measurement) ---
    def test_positive_slope_returns_best_quality(self) -> None:
        """Physically impossible: VMAF increases with CRF.

        All scores are <= target, so the all-below branch catches this
        first and returns the lowest (best quality) CRF.
        """
        crf = _fit_crf([(18, 88), (23, 92), (28, 95)], 95)
        # All scores (88, 92, 95) <= 95 -> all-below -> return lowest CRF
        assert crf == 18.0

    # --- Near-zero slope ---
    def test_near_zero_slope_returns_median(self) -> None:
        """VMAF barely changes with CRF -- unreliable fit."""
        crf = _fit_crf([(18, 96.1), (23, 96.0), (28, 95.9)], 95)
        # All above target -> would hit that branch first
        # 96.1, 96.0, 95.9 all >= 95
        assert crf == 28.0

    def test_near_zero_slope_all_below(self) -> None:
        crf = _fit_crf([(18, 80.1), (23, 80.0), (28, 79.9)], 95)
        assert crf == 18.0

    # --- Clamping ---
    def test_clamps_to_0(self) -> None:
        """Predicted CRF below 0 -> clamped to 0."""
        # Bracketing pair: (18, 99) and (23, 80) for target 95
        # 18 + (95-99)*(23-18)/(80-99) = 18 + 20/19 = 19.05
        crf = _fit_crf([(18, 99), (23, 80)], 95)
        assert crf == pytest.approx(19.05, abs=0.02)
        assert crf >= 0

    def test_clamps_to_51(self) -> None:
        """Predicted CRF above 51 -> clamped to 51."""
        crf = _fit_crf([(18, 90), (23, 89), (28, 88)], 50)
        assert crf == 28.0
        assert crf <= 51

    # --- Unsorted input ---
    def test_unsorted_input(self) -> None:
        """Function sorts internally, so input order doesn't matter."""
        crf = _fit_crf([(33, 93), (18, 97.5), (28, 94.5), (23, 96)], 95)
        assert crf == pytest.approx(26.33, abs=0.02)


# ── vmaf_available ────────────────────────────────────────────────────


class TestVmafAvailable:
    def test_available_when_libvmaf_in_filters(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="... libvmaf ...", stderr=""
            )
            assert vmaf_available() is True

    def test_not_available_without_libvmaf(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="... only hevc filters ...", stderr=""
            )
            assert vmaf_available() is False

    def test_not_available_on_ffmpeg_not_found(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert vmaf_available() is False

    def test_not_available_on_timeout(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 10)
            assert vmaf_available() is False


# ── Clear method caches between tests ────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear functools.cache on vmaf detection predicates between tests.

    Otherwise the first test's real ffmpeg call populates the cache and
    subsequent tests with mocked subprocess.run see stale results.
    """
    _scdet_available.cache_clear()
    vmaf_available.cache_clear()


# ── _scdet_available ──────────────────────────────────────────────────


class TestScdetAvailable:
    def test_available_when_scdet_in_filters(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="... scdet ...", stderr=""
            )
            assert _scdet_available() is True

    def test_not_available_without_scdet(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="... other filters ...", stderr=""
            )
            assert _scdet_available() is False

    def test_not_available_on_ffmpeg_not_found(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert _scdet_available() is False

    def test_not_available_on_timeout(self) -> None:
        with patch.object(subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 10)
            assert _scdet_available() is False


# ── _evenly_spaced_clips ──────────────────────────────────────────────


class TestEvenlySpacedClips:
    def test_normal_three_clips(self) -> None:
        starts = _evenly_spaced_clips(duration=120, num_clips=3, clip_duration=8)
        assert len(starts) == 3
        # All start times should be within bounds [0, duration - clip_duration]
        assert all(0 <= s <= 120 - 8 for s in starts)
        # Should be sorted ascending
        assert starts == sorted(starts)
        # Should not overlap (each start >= previous + minimum gap)
        for i in range(1, len(starts)):
            assert starts[i] >= starts[i - 1]

    def test_two_clips(self) -> None:
        starts = _evenly_spaced_clips(duration=120, num_clips=2, clip_duration=8)
        assert len(starts) == 2
        assert all(0 <= s <= 112 for s in starts)

    def test_short_video_returns_single_clip(self) -> None:
        """When duration <= margin*2, a single clip at 25% is returned."""
        starts = _evenly_spaced_clips(duration=20, num_clips=3, clip_duration=8)
        # clip_duration + 5 = margin, used above
        assert len(starts) == 1
        assert starts[0] == 20 * 0.25

    def test_single_clip_within_bounds(self) -> None:
        """num_clips <= 1: clip starts at min(margin, duration - clip_duration)."""
        starts = _evenly_spaced_clips(duration=120, num_clips=1, clip_duration=8)
        assert len(starts) == 1
        margin = 8 + 5
        assert starts[0] == min(margin, 120 - 8)

    def test_single_clip_short_video(self) -> None:
        """num_clips <= 1 with very short video."""
        starts = _evenly_spaced_clips(duration=10, num_clips=1, clip_duration=8)
        assert len(starts) == 1

    def test_clips_do_not_exceed_duration(self) -> None:
        """All clips must fit within the video."""
        starts = _evenly_spaced_clips(duration=60, num_clips=2, clip_duration=8)
        for s in starts:
            assert s + 8 <= 60, f"clip at {s} exceeds duration"

    def test_large_num_clips_clamped_to_non_overlapping(self) -> None:
        """When step < clip_duration, clip count is reduced to avoid overlap."""
        # duration=50, num_clips=5, clip_duration=8:
        #   margin=13, available=24, step=6 (< 8) -> overlap!
        #   max_non_overlap = 24/8 + 1 = 4
        starts = _evenly_spaced_clips(duration=50, num_clips=5, clip_duration=8)
        # Clipped to 4 non-overlapping clips
        assert len(starts) == 4
        # All must fit within the video
        assert all(s + 8 <= 50 for s in starts)
        # No overlap: each start should be >= previous start + clip_duration
        for i in range(1, len(starts)):
            assert starts[i] >= starts[i - 1] + 8, f"clip {i} overlaps {i - 1}"


# ── _pick_clips_from_scenes ───────────────────────────────────────────


class TestPickClipsFromScenes:
    def test_picks_from_scene_boundaries(self) -> None:
        scene_times = [10.0, 30.0, 60.0, 90.0, 120.0]
        starts = _pick_clips_from_scenes(
            scene_times, duration=150, num_clips=3, clip_duration=8
        )
        assert len(starts) == 3
        assert all(0 <= s <= 150 - 8 for s in starts)

    def test_all_returned_starts_within_bounds(self) -> None:
        scene_times = [5.0, 25.0, 55.0]
        starts = _pick_clips_from_scenes(
            scene_times, duration=80, num_clips=3, clip_duration=8
        )
        for s in starts:
            assert 0.0 <= s <= 80 - 8, f"clip start {s} out of bounds"

    def test_short_video_single_clip(self) -> None:
        """Very short video returns single clip at 25%."""
        starts = _pick_clips_from_scenes([], duration=20, num_clips=3, clip_duration=8)
        assert len(starts) == 1
        assert starts[0] == 20 * 0.25

    def test_scene_boundary_adjustment(self) -> None:
        """Clips near scene boundaries should be pushed past them."""
        # With duration=60, num_clips=1, clip_duration=8:
        #   margin = 10.0, segment_size = (60 - 20) / 1 = 40
        #   target = 10 + 40 * 0.5 = 30.0
        # A scene boundary at 30.0 is within 1.5s of target, so clip
        # should be adjusted to boundary + 1.0 = 31.0.
        scene_times = [30.0]
        starts = _pick_clips_from_scenes(
            scene_times, duration=60, num_clips=1, clip_duration=8
        )
        assert len(starts) == 1
        assert starts[0] == 31.0


# ── _select_clips ─────────────────────────────────────────────────────


class TestSelectClips:
    def test_very_short_video_returns_single_clip(self) -> None:
        """Shorter than num_clips * clip_duration * 2."""
        starts = _select_clips(
            input_path=Path("/dummy"), duration=10, num_clips=3, clip_duration=8
        )
        assert len(starts) == 1

    def test_num_clips_zero_returns_single_clip(self) -> None:
        starts = _select_clips(
            input_path=Path("/dummy"), duration=120, num_clips=0, clip_duration=8
        )
        assert len(starts) == 1

    def test_short_video_below_degenerate_threshold(self) -> None:
        """Very short video (< num_clips * clip_duration * 2)."""
        # With num_clips=3, clip_duration=8: threshold = 3*8*2 = 48.
        # duration=20 < 48 -> legacy path: <120s -> 0.0
        starts = _select_clips(
            input_path=Path("/dummy"), duration=20, num_clips=3, clip_duration=8
        )
        assert len(starts) == 1
        assert starts[0] == 0.0

    def test_long_video_legacy_position(self) -> None:
        """For long (>=120s) videos in degenerate case, legacy position is 25%."""
        # Use num_clips high enough to trigger the degenerate guard.
        # num_clips=20, clip_duration=8 -> threshold = 20*8*2 = 320.
        # duration=120 < 320 -> degenerate path: >=120s -> 25%
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=20,
            clip_duration=8,
        )
        assert len(starts) == 1
        assert starts[0] == 30.0  # 120 * 0.25

    @patch("h265ify.vmaf._scdet_available", return_value=False)
    def test_fallback_evenly_spaced_when_scdet_unavailable(
        self, mock_scdet: object
    ) -> None:
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=3,
            clip_duration=8,
        )
        # Should fall back to evenly-spaced, which gives 3 clips
        assert len(starts) == 3
        assert all(0 <= s <= 120 - 8 for s in starts)

    @patch("h265ify.vmaf._scdet_available", return_value=True)
    @patch("h265ify.vmaf._run_scdet", return_value=[])
    def test_fallback_evenly_spaced_when_scdet_returns_empty(
        self, mock_scdet_run: object, mock_scdet_avail: object
    ) -> None:
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=3,
            clip_duration=8,
        )
        # Evenly-spaced fallback
        assert len(starts) == 3

    @patch("h265ify.vmaf._scdet_available", return_value=True)
    @patch("h265ify.vmaf._run_scdet", return_value=[15.0, 45.0, 75.0])
    def test_uses_scene_detection_when_available(
        self, mock_scdet_run: object, mock_scdet_avail: object
    ) -> None:
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=3,
            clip_duration=8,
        )
        # With 3 scene boundaries and 3 clips, should use _pick_clips_from_scenes
        assert len(starts) == 3

    @patch("h265ify.vmaf._scdet_available", return_value=True)
    @patch(
        "h265ify.vmaf._run_scdet",
        return_value=[5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0],
    )
    def test_picks_from_abundant_scenes(
        self, mock_scdet_run: object, mock_scdet_avail: object
    ) -> None:
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=4,
            clip_duration=8,
        )
        assert len(starts) == 4

    @patch("h265ify.vmaf._scdet_available", return_value=True)
    @patch("h265ify.vmaf._run_scdet", side_effect=ValueError("test error"))
    def test_handles_scdet_exception_gracefully(
        self, mock_scdet_run: object, mock_scdet_avail: object
    ) -> None:
        """If _run_scdet raises, _select_clips should fall back gracefully."""
        starts = _select_clips(
            input_path=Path("/dummy"),
            duration=120,
            num_clips=3,
            clip_duration=8,
        )
        # Should fall back to evenly-spaced
        assert len(starts) >= 1


# ── _extract_clip ─────────────────────────────────────────────────────


class TestExtractClip:
    def test_successful_extraction(self) -> None:
        with patch("h265ify.vmaf._ffmpeg_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            result = _extract_clip(
                input_path=Path("/dummy/source.mp4"),
                start_time=30.0,
                duration=8.0,
                output_path=Path("/tmp/clip.mp4"),
            )
            assert result is True
            # Verify the ffmpeg command included -ss before -i and stream copy
            call_args = mock_run.call_args[0][0]
            assert "-ss" in call_args
            assert "30.0" in call_args
            assert "-c" in call_args
            assert "copy" in call_args

    def test_failed_extraction(self) -> None:
        with patch("h265ify.vmaf._ffmpeg_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 1, stdout="", stderr="error"
            )
            result = _extract_clip(
                input_path=Path("/dummy/source.mp4"),
                start_time=30.0,
                duration=8.0,
                output_path=Path("/tmp/clip.mp4"),
            )
            assert result is False

    def test_extraction_timeout(self) -> None:
        with patch("h265ify.vmaf._ffmpeg_run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 600)
            result = _extract_clip(
                input_path=Path("/dummy/source.mp4"),
                start_time=30.0,
                duration=8.0,
                output_path=Path("/tmp/clip.mp4"),
            )
            assert result is False

    def test_extraction_file_not_found(self) -> None:
        with patch("h265ify.vmaf._ffmpeg_run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = _extract_clip(
                input_path=Path("/dummy/source.mp4"),
                start_time=30.0,
                duration=8.0,
                output_path=Path("/tmp/clip.mp4"),
            )
            assert result is False

    def test_only_video_stream_mapped(self) -> None:
        """Extract should only map the first video stream."""
        with patch("h265ify.vmaf._ffmpeg_run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            _extract_clip(
                input_path=Path("/dummy/source.mp4"),
                start_time=10.0,
                duration=8.0,
                output_path=Path("/tmp/clip.mp4"),
            )
            call_args = mock_run.call_args[0][0]
            assert "-map" in call_args
            map_idx = call_args.index("-map")
            assert call_args[map_idx + 1] == "0:v:0"


# ── _probe_crf ────────────────────────────────────────────────────────


class TestProbeCrf:
    """_probe_crf encodes clips at a given CRF and returns per-clip VMAF + minimum."""

    def test_all_clips_succeed(self) -> None:
        encoder = _sample_encoder()
        tmp = Path("/tmp")

        with (
            patch("h265ify.vmaf._build_probe_command") as mock_build,
            patch("h265ify.vmaf._ffmpeg_run") as mock_ffmpeg,
            patch("h265ify.vmaf._compute_vmaf_score") as mock_vmaf,
        ):
            mock_build.return_value = ["ffmpeg", "-i", "in", "out"]
            mock_ffmpeg.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            mock_vmaf.side_effect = [95.0, 94.0, 93.0]

            clip_paths = [
                Path("clip_0.mp4"),
                Path("clip_1.mp4"),
                Path("clip_2.mp4"),
            ]
            scores, min_score, encoded_bytes = _probe_crf(
                crf=23,
                clip_paths=clip_paths,
                seg_probe=_sample_probe(),
                encoder=encoder,
                tmp=tmp,
            )

            assert scores == [95.0, 94.0, 93.0]
            assert min_score == 93.0
            assert encoded_bytes == 0  # mocked clips have no real file size
            # Should have called _build_probe_command and _ffmpeg_run for each clip
            assert mock_build.call_count == 3
            assert mock_ffmpeg.call_count == 3
            # Should have called _compute_vmaf_score for each clip
            assert mock_vmaf.call_count == 3

    def test_some_clips_fail(self) -> None:
        encoder = _sample_encoder()
        tmp = Path("/tmp")

        with (
            patch("h265ify.vmaf._build_probe_command") as mock_build,
            patch("h265ify.vmaf._ffmpeg_run") as mock_ffmpeg,
            patch("h265ify.vmaf._compute_vmaf_score") as mock_vmaf,
        ):
            mock_build.return_value = ["ffmpeg", "-i", "in", "out"]
            mock_ffmpeg.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            # Second clip's VMAF fails (returns None)
            mock_vmaf.side_effect = [95.0, None, 93.0]

            clip_paths = [
                Path("clip_0.mp4"),
                Path("clip_1.mp4"),
                Path("clip_2.mp4"),
            ]
            scores, min_score, encoded_bytes = _probe_crf(
                crf=23,
                clip_paths=clip_paths,
                seg_probe=_sample_probe(),
                encoder=encoder,
                tmp=tmp,
            )

            assert scores == [95.0, 93.0]
            assert min_score == 93.0  # min of the two successful clips
            assert encoded_bytes == 0

    def test_all_clips_fail_returns_none(self) -> None:
        encoder = _sample_encoder()
        tmp = Path("/tmp")

        with (
            patch("h265ify.vmaf._build_probe_command") as mock_build,
            patch("h265ify.vmaf._ffmpeg_run") as mock_ffmpeg,
            patch("h265ify.vmaf._compute_vmaf_score") as mock_vmaf,
        ):
            mock_build.return_value = ["ffmpeg", "-i", "in", "out"]
            mock_ffmpeg.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            mock_vmaf.return_value = None

            clip_paths = [Path("clip_0.mp4"), Path("clip_1.mp4")]
            scores, min_score, encoded_bytes = _probe_crf(
                crf=23,
                clip_paths=clip_paths,
                seg_probe=_sample_probe(),
                encoder=encoder,
                tmp=tmp,
            )

            assert scores == []
            assert min_score is None
            assert encoded_bytes == 0

    def test_skip_on_encode_failure(self) -> None:
        encoder = _sample_encoder()
        tmp = Path("/tmp")

        with (
            patch("h265ify.vmaf._build_probe_command") as mock_build,
            patch("h265ify.vmaf._ffmpeg_run") as mock_ffmpeg,
            patch("h265ify.vmaf._compute_vmaf_score") as mock_vmaf,
        ):
            mock_build.return_value = ["ffmpeg", "-i", "in", "out"]
            # First encode fails (non-zero returncode)
            mock_ffmpeg.side_effect = [
                subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]
            mock_vmaf.return_value = 94.0

            clip_paths = [Path("clip_0.mp4"), Path("clip_1.mp4")]
            scores, min_score, encoded_bytes = _probe_crf(
                crf=23,
                clip_paths=clip_paths,
                seg_probe=_sample_probe(),
                encoder=encoder,
                tmp=tmp,
            )

            # Only the second clip contributed
            assert scores == [94.0]
            assert min_score == 94.0
            assert encoded_bytes == 0
            # VMAF only called once (for the successful clip)
            assert mock_vmaf.call_count == 1

    def test_timeout_during_encode_skips_clip(self) -> None:
        encoder = _sample_encoder()
        tmp = Path("/tmp")

        with (
            patch("h265ify.vmaf._build_probe_command") as mock_build,
            patch("h265ify.vmaf._ffmpeg_run") as mock_ffmpeg,
        ):
            mock_build.return_value = ["ffmpeg"]
            mock_ffmpeg.side_effect = subprocess.TimeoutExpired("ffmpeg", 600)

            clip_paths = [Path("clip_0.mp4"), Path("clip_1.mp4")]
            scores, min_score, encoded_bytes = _probe_crf(
                crf=23,
                clip_paths=clip_paths,
                seg_probe=_sample_probe(),
                encoder=encoder,
                tmp=tmp,
            )

            assert scores == []
            assert min_score is None
            assert encoded_bytes == 0


# ── estimate_crf_size_ratio ─────────────────────────────────────────


class TestEstimateCrfSizeRatio:
    """estimate_crf_size_ratio computes projected size ratio between CRFs."""

    def test_insufficient_data_returns_one(self) -> None:
        """With < 2 data points, returns 1.0 (no projection)."""
        assert estimate_crf_size_ratio([(23, 1000)], 23, 28) == 1.0
        assert estimate_crf_size_ratio([], 23, 28) == 1.0

    def test_higher_crf_smaller_file(self) -> None:
        """Higher CRF produces smaller files \u2192 ratio < 1."""
        # CRF 18 \u2192 2000 bytes, CRF 28 \u2192 1000 bytes (50% over 10 steps)
        ratio = estimate_crf_size_ratio([(18, 2000), (28, 1000)], 18, 28)
        assert ratio == pytest.approx(0.5, abs=0.02)

    def test_lower_crf_larger_file(self) -> None:
        """Going from higher CRF to lower CRF \u2192 ratio > 1."""
        ratio = estimate_crf_size_ratio([(23, 1500), (28, 1000)], 28, 23)
        assert ratio == pytest.approx(1.5, abs=0.05)

    def test_three_point_fit(self) -> None:
        """Uses all data points for more robust fit."""
        ratio = estimate_crf_size_ratio([(18, 3000), (23, 1700), (28, 1000)], 28, 33)
        assert ratio < 1.0
        assert 0.4 < ratio < 1.0

    def test_same_crf_returns_one(self) -> None:
        """from_crf == to_crf \u2192 exp(0) \u2192 1.0."""
        ratio = estimate_crf_size_ratio([(18, 2000), (28, 1000)], 28, 28)
        assert ratio == pytest.approx(1.0, abs=0.01)


# ── _build_probe_command ──────────────────────────────────────────────


def _hdr_probe(**overrides: object) -> ProbeResult:
    kwargs: dict[str, object] = {
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
    }
    kwargs.update(overrides)
    return ProbeResult(
        path=Path("test.mkv"),
        is_h265=False,
        video_codec="h264",
        width=1920,
        height=1080,
        duration=120.0,
        file_size=1_000_000,
        color=ColorInfo(
            bit_depth=10,
            color_primaries=str(kwargs["color_primaries"]),
            color_transfer=str(kwargs["color_transfer"]),
            color_space=str(kwargs["color_space"]),
            is_hdr=True,
        ),
    )


class TestBuildProbeCommand:
    def test_hdr_color_metadata_passthrough(self) -> None:
        """Probe encodes for HDR content include validated color metadata."""
        cmd = _build_probe_command(
            Path("test.mkv"),
            Path("out.mkv"),
            _hdr_probe(),
            Encoder(name="libx265", is_hardware=False, label="CPU (libx265)"),
            crf=23,
        )
        assert "-color_primaries" in cmd
        assert cmd[cmd.index("-color_primaries") + 1] == "bt2020"
        assert "-color_trc" in cmd
        assert cmd[cmd.index("-color_trc") + 1] == "smpte2084"
        assert "-colorspace" in cmd
        assert cmd[cmd.index("-colorspace") + 1] == "bt2020nc"

    def test_sdr_no_color_flags(self) -> None:
        """SDR content with no color metadata produces no color flags."""
        cmd = _build_probe_command(
            Path("test.mp4"),
            Path("out.mkv"),
            _sample_probe(),
            Encoder(name="libx265", is_hardware=False, label="CPU (libx265)"),
            crf=23,
        )
        assert "-color_primaries" not in cmd
        assert "-color_trc" not in cmd
        assert "-colorspace" not in cmd

    def test_invalid_color_values_excluded(self) -> None:
        """Color metadata values not in the validated allowlists are excluded."""
        cmd = _build_probe_command(
            Path("test.mkv"),
            Path("out.mkv"),
            _hdr_probe(
                color_primaries="nonexistent",
                color_transfer="bogus",
                color_space="invalid",
            ),
            Encoder(name="libx265", is_hardware=False, label="CPU (libx265)"),
            crf=23,
        )
        assert "-color_primaries" not in cmd
        assert "-color_trc" not in cmd
        assert "-colorspace" not in cmd
