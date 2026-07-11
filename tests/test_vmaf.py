"""Tests for vmaf.py - VMAF-based auto-CRF detection."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from h265ify.vmaf import (
    _VMAF_ABORTED,
    _fit_crf,
    kill_all_vmaf_procs,
    vmaf_available,
)


class TestFitCrf:
    """_fit_crf is a pure function: (crf_scores, target) -> optimal_crf."""

    # --- Normal bracket ---
    def test_normal_bracket(self) -> None:
        """Scores straddle target; linear fit picks between them."""
        # VMAF drops ~3 per 5 CRF steps from 97.5 at 18 → 93 at 33
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 95)
        assert crf == 26

    def test_normal_bracket_target_lower(self) -> None:
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 93)
        assert crf == 33

    def test_normal_bracket_target_higher(self) -> None:
        crf = _fit_crf([(18, 97.5), (23, 96), (28, 94.5), (33, 93)], 98)
        assert crf == 18

    # --- All above target → highest CRF ---
    def test_all_above_returns_highest(self) -> None:
        crf = _fit_crf([(18, 99), (23, 98), (28, 97)], 95)
        assert crf == 28

    def test_all_above_with_only_one(self) -> None:
        crf = _fit_crf([(18, 99)], 95)
        assert crf == 18

    # --- All below target → lowest CRF ---
    def test_all_below_returns_lowest(self) -> None:
        crf = _fit_crf([(28, 93), (33, 90)], 95)
        assert crf == 28

    def test_all_below_with_only_one(self) -> None:
        crf = _fit_crf([(33, 90)], 95)
        assert crf == 33

    # --- Edge cases ---
    def test_empty_scores_returns_23(self) -> None:
        assert _fit_crf([], 95) == 23

    def test_single_point_returns_that_crf(self) -> None:
        crf = _fit_crf([(23, 95)], 95)
        assert crf == 23

    def test_flat_scores_all_at_target(self) -> None:
        """All scores equal to target → all-above branch returns highest."""
        crf = _fit_crf([(18, 95), (23, 95), (28, 95)], 95)
        assert crf == 28

    def test_flat_scores_all_below(self) -> None:
        """Flat scores all below target → all-below branch returns lowest."""
        crf = _fit_crf([(18, 80), (23, 80), (28, 80)], 95)
        assert crf == 18

    # --- Positive slope (invalid measurement) ---
    def test_positive_slope_returns_best_quality(self) -> None:
        """Physically impossible: VMAF increases with CRF.

        All scores are ≤ target, so the all-below branch catches this
        first and returns the lowest (best quality) CRF.
        """
        crf = _fit_crf([(18, 88), (23, 92), (28, 95)], 95)
        # All scores (88, 92, 95) ≤ 95 → all-below → return lowest CRF
        assert crf == 18

    # --- Near-zero slope ---
    def test_near_zero_slope_returns_median(self) -> None:
        """VMAF barely changes with CRF — unreliable fit."""
        crf = _fit_crf([(18, 96.1), (23, 96.0), (28, 95.9)], 95)
        # All above target → would hit that branch first
        # 96.1, 96.0, 95.9 all >= 95
        assert crf == 28

    def test_near_zero_slope_all_below(self) -> None:
        crf = _fit_crf([(18, 80.1), (23, 80.0), (28, 79.9)], 95)
        assert crf == 18

    # --- Clamping ---
    def test_clamps_to_0(self) -> None:
        """Predicted CRF below 0 → clamped to 0."""
        # Very steep negative slope at low CRFs
        crf = _fit_crf([(18, 99), (23, 80)], 95)
        assert crf >= 0

    def test_clamps_to_51(self) -> None:
        """Predicted CRF above 51 → clamped to 51."""
        crf = _fit_crf([(18, 90), (23, 89), (28, 88)], 50)
        assert crf <= 51

    # --- Unsorted input ---
    def test_unsorted_input(self) -> None:
        """Function sorts internally, so input order doesn't matter."""
        crf = _fit_crf([(33, 93), (18, 97.5), (28, 94.5), (23, 96)], 95)
        assert crf == 26


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


class TestKillAllVmafProcs:
    def test_sets_abort_flag(self) -> None:
        _VMAF_ABORTED.clear()
        assert not _VMAF_ABORTED.is_set()
        kill_all_vmaf_procs()
        assert _VMAF_ABORTED.is_set()
        _VMAF_ABORTED.clear()  # clean up for other tests

    def test_safe_when_no_procs(self) -> None:
        _VMAF_ABORTED.clear()
        kill_all_vmaf_procs()  # should not raise
        assert _VMAF_ABORTED.is_set()
        _VMAF_ABORTED.clear()