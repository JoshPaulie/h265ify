"""VMAF-based auto-CRF detection for optimal encoding quality.

Uses ffmpeg's libvmaf filter to measure perceptual quality at several CRF
values on short sample clips, then fits a curve to find the CRF that achieves a
target VMAF score (default 95, near-transparent quality).

Multiple clips are extracted from different scenes (via ffmpeg's scdet filter)
and the *minimum* VMAF across clips is used for each CRF, ensuring the hardest
sampled scene drives the recommendation.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from .hardware import Encoder, encoder_quality_flags, pix_fmt_for_encoder
from .logger import logger
from .probe import ProbeResult

# Candidate CRF values for VMAF probing (covers roughly VMAF 98 down to VMAF 85).
# These span the useful quality range for most content.
_CANDIDATE_CRFS = [18, 23, 28, 33]

# Default target VMAF score (95 = near-transparent, indistinguishable from source).
_DEFAULT_TARGET_VMAF = 95.0

# Duration of each extracted test clip (seconds).  Multiple short clips from
# different scenes give better coverage of content complexity than a single
# longer segment.
_CLIP_DURATION = 8.0

# Number of sample clips to extract for VMAF evaluation across the video.
_NUM_CLIPS = 3

# Timeout for the full-video scene-detection pass (seconds).
# At ~15x realtime for a 360p scan, a 2.5h movie takes ~10 minutes.
_SCENE_DETECT_TIMEOUT = 600.0

# Regex for parsing ffmpeg scdet filter output.
_SCENE_RE = re.compile(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)")


def _ffmpeg_run(
    cmd: list[str],
    timeout: float | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return the result."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


@functools.cache
def _scdet_available() -> bool:
    """Return True if ffmpeg has the scdet filter."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "scdet" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_scdet(input_path: Path) -> list[float]:
    """Run ffmpeg scdet and return sorted scene-change timestamps.

    Returns an empty list if detection fails for any reason.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(input_path),
        "-vf",
        "scale=-2:360,scdet=threshold=10",
        "-f",
        "null",
        "-",
    ]
    try:
        result = _ffmpeg_run(cmd, timeout=_SCENE_DETECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []

    # Parse lavfi.scd.score / lavfi.scd.time lines from stderr
    timestamps: list[float] = []
    for line in result.stderr.splitlines():
        m = _SCENE_RE.search(line)
        if m:
            score = float(m.group(1))
            ts = float(m.group(2))
            if score > 10.0:
                timestamps.append(ts)

    if not timestamps:
        return []
    timestamps.sort()

    # Group detections within 1-second windows (same scene change event)
    grouped: list[float] = [timestamps[0]]
    for t in timestamps[1:]:
        if t - grouped[-1] > 1.0:
            grouped.append(t)
    return grouped


def _evenly_spaced_clips(
    duration: float,
    num_clips: int,
    clip_duration: float,
) -> list[float]:
    """Return evenly-spaced clip start times, avoiding the edges."""
    margin = clip_duration + 5.0
    if duration <= margin * 2:
        return [duration * 0.25]
    if num_clips <= 1:
        return [min(margin, duration - clip_duration)]
    available = duration - 2.0 * margin
    step = available / (num_clips - 1)

    # Clamp to non-overlapping: each clip start must be at least
    # *clip_duration* from the previous one so they don't overlap.
    if step < clip_duration:
        max_non_overlap = max(1, int(available / clip_duration) + 1)
        # Recalculate with the reduced clip count
        if max_non_overlap <= 1:
            return [margin]
        step = available / (max_non_overlap - 1)
        num_clips = max_non_overlap

    return [margin + i * step for i in range(num_clips)]


def _pick_clips_from_scenes(
    scene_times: list[float],
    duration: float,
    num_clips: int,
    clip_duration: float,
) -> list[float]:
    """Pick clip start times using scene boundaries to avoid transition frames.

    Divides the video into *num_clips* equal time ranges.  For each range,
    picks the scene boundary nearest the centre of that range, then places
    the clip 1s into the new scene.
    """
    margin = clip_duration + 2.0
    if duration <= margin * 2:
        return [duration * 0.25]

    segment_size = (duration - 2.0 * margin) / num_clips
    starts: list[float] = []

    for i in range(num_clips):
        target = margin + segment_size * (i + 0.5)

        # If target lands near a scene boundary, push past it so the clip
        # doesn't straddle a transition.
        adjusted = target
        for t in scene_times:
            if abs(t - target) < 1.5:
                adjusted = t + 1.0
                break

        adjusted = min(adjusted, duration - clip_duration)
        adjusted = max(0.0, adjusted)
        starts.append(adjusted)

    return starts


def _select_clips(
    input_path: Path,
    duration: float,
    num_clips: int = _NUM_CLIPS,
    clip_duration: float = _CLIP_DURATION,
) -> list[float]:
    """Select representative clip start times using scene detection.

    Uses ffmpeg's ``scdet`` filter to find scene boundaries, then picks
    *num_clips* clips spread across the video from distinct scenes.

    Falls back to evenly-spaced positions if scdet is unavailable,
    times out, or returns too few boundaries.

    For very short videos (< *num_clips* \u00d7 *clip_duration* \u00d7 2) a
    single clip at the legacy 25% position is returned.
    """
    # Degenerate param or very short video -- single clip, legacy position
    if num_clips < 1 or duration < num_clips * clip_duration * 2:
        return [duration * 0.25 if duration >= 120 else 0.0]

    # Try scene detection
    scene_times: list[float] = []
    if _scdet_available():
        try:
            scene_times = _run_scdet(input_path)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass

    if len(scene_times) >= num_clips:
        return _pick_clips_from_scenes(scene_times, duration, num_clips, clip_duration)

    # Fallback: evenly-spaced clips
    if not scene_times:
        logger.info(
            "auto-CRF: scdet unavailable or returned no scene boundaries,"
            f" using evenly-spaced clips (num_clips={num_clips})"
        )
    else:
        logger.info(
            "auto-CRF: scdet returned fewer scene boundaries"
            f" ({len(scene_times)}) than requested clips ({num_clips}),"
            " using evenly-spaced clips"
        )
    return _evenly_spaced_clips(duration, num_clips, clip_duration)


@functools.cache
def vmaf_available() -> bool:
    """Return True if ffmpeg has libvmaf support."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "libvmaf" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _extract_clip(
    input_path: Path,
    start_time: float,
    duration: float,
    output_path: Path,
) -> bool:
    """Extract a video clip starting at *start_time* for *duration* seconds.

    Uses stream copy so no quality is lost.  Only the first video stream
    is extracted (no audio, no subtitles).
    """
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start_time),
        "-i",
        str(input_path),
        "-t",
        str(duration),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        str(output_path),
    ]
    try:
        result = _ffmpeg_run(cmd, timeout=600)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _build_probe_command(
    input_path: Path,
    output_path: Path,
    probe: ProbeResult,
    encoder: Encoder,
    crf: int,
    preset: str = "medium",
) -> list[str]:
    """Build a minimal ffmpeg command for CRF probing.

    Video-only encode -- no audio, subtitles, hvc1 tags, or faststart.
    Just the bare minimum to measure quality at a given CRF.
    Uses the same preset as the main encode for accurate quality assessment.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    cmd.extend(["-i", str(input_path)])
    cmd.extend(["-map", "0:v:0"])
    cmd.extend(["-c:v", encoder.name])
    cmd.extend(encoder_quality_flags(encoder.name, crf, preset=preset))

    # Pixel format / bit depth (shared helper avoids encoder/bit-depth duplication)
    pix_fmt = pix_fmt_for_encoder(encoder.name, probe.color.bit_depth)
    if pix_fmt is not None:
        cmd.extend(["-pix_fmt", pix_fmt])

    cmd.extend(["-an", "-sn"])  # no audio, no subtitles
    cmd.append(str(output_path))
    return cmd


def _compute_vmaf_score(reference: Path, distorted: Path) -> float | None:
    """Compute the mean VMAF score (0-100) between two video files.

    Uses ffmpeg's libvmaf filter with JSON output.  Returns None if
    computation fails for any reason.
    """
    # Use a temp directory with a fixed log filename instead of embedding
    # an absolute path in the ffmpeg filter string (which would break with
    # spaces or special characters in TMPDIR).
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_name = "vmaf_log.json"
        log_path = Path(tmp_dir) / log_name

        try:
            cmd: list[str] = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(reference),
                "-i",
                str(distorted),
                "-filter_complex",
                f"[0:v][1:v]libvmaf=log_path={log_name}:log_fmt=json",
                "-f",
                "null",
                "-",
            ]
            result = _ffmpeg_run(cmd, timeout=600, cwd=tmp_dir)
            if result.returncode != 0:
                logger.warning(f"VMAF computation failed: {result.stderr[:200]}")
                return None

            if not log_path.exists():
                return None

            data = json.loads(log_path.read_text())

            # VMAF v3+ format: pooled_metrics.vmaf.mean
            pooled = data.get("pooled_metrics", {})
            vmaf_metrics = pooled.get("vmaf", {})
            mean_score = vmaf_metrics.get("mean")
            if mean_score is not None:
                return float(mean_score)

            # Fallback: average per-frame scores (older VMAF format)
            frames = data.get("frames", [])
            if frames:
                scores: list[float] = [
                    float(f.get("metrics", {}).get("vmaf", 0))
                    for f in frames
                    if "vmaf" in f.get("metrics", {})
                ]
                if scores:
                    return sum(scores) / len(scores)

            return None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            logger.warning(f"VMAF computation error: {e}")
            return None
        # TempDirectory __exit__ handles cleanup


def _fit_crf(crf_scores: list[tuple[int, float]], target_vmaf: float) -> float:
    """Find the CRF value that achieves *target_vmaf*.

    When scores bracket the target (one above, one below), linearly interpolates
    between the two bracketing scores for a more precise recommendation.
    Falls back to linear regression when no bracket exists.
    Clamped to [0, 51].
    """
    if not crf_scores:
        return 23.0

    crf_scores.sort(key=lambda x: x[0])
    n = len(crf_scores)

    # All scores above target \u2192 use the highest tested CRF (smallest file)
    # since even the worst-quality tested CRF still meets the target quality.
    if all(vmaf >= target_vmaf for _, vmaf in crf_scores):
        return float(crf_scores[-1][0])

    # All scores below target \u2192 use the lowest CRF (best quality)
    # since even the best-quality tested CRF is below the target.
    if all(vmaf <= target_vmaf for _, vmaf in crf_scores):
        return float(crf_scores[0][0])

    # Find bracketing pair and linearly interpolate
    for i in range(1, n):
        crf_low, vmaf_low = crf_scores[i - 1]
        crf_high, vmaf_high = crf_scores[i]

        # Check if these two points bracket the target
        if (vmaf_low >= target_vmaf >= vmaf_high) or (
            vmaf_low <= target_vmaf <= vmaf_high
        ):
            if vmaf_high == vmaf_low:
                result = float(crf_low)
            else:
                result = crf_low + (target_vmaf - vmaf_low) * (crf_high - crf_low) / (
                    vmaf_high - vmaf_low
                )
            return max(0.0, min(51.0, result))

    # Fallback: linear regression (shouldn't normally reach here)
    sum_x = sum(crf for crf, _ in crf_scores)
    sum_y = sum(vmaf for _, vmaf in crf_scores)
    sum_xy = sum(crf * vmaf for crf, vmaf in crf_scores)
    sum_xx = sum(crf * crf for crf, _ in crf_scores)

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return float(crf_scores[n // 2][0])

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    if slope >= 0:
        logger.warning(
            f"auto-CRF: unexpected positive slope ({slope:.4f}), using median"
        )
        return float(crf_scores[n // 2][0])

    if abs(slope) < 0.01:
        logger.warning(
            f"auto-CRF: near-zero slope ({slope:.4f}), VMAF scores"
            f" barely change with CRF ({crf_scores}), using median"
        )
        return float(crf_scores[n // 2][0])

    predicted = (target_vmaf - intercept) / slope
    return max(0.0, min(51.0, predicted))


def _probe_crf(
    crf: int,
    clip_paths: list[Path],
    seg_probe: ProbeResult,
    encoder: Encoder,
    tmp: Path,
    preset: str = "medium",
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[float], float | None, int]:
    """Encode all *clip_paths* at *crf* and return per-clip VMAF scores + minimum.

    Returns (clip_scores, min_score, total_encoded_bytes).
    *min_score* is None if all encodes or all VMAF computations failed.
    """
    clip_scores: list[float] = []
    total_encoded_bytes = 0

    for i, clip_path in enumerate(clip_paths):
        encoded = tmp / f"crf_{crf}_clip_{i}{clip_path.suffix}"

        if progress_callback:
            progress_callback(
                f"encoding clip {i + 1}/{len(clip_paths)} at CRF {crf}..."
            )

        cmd = _build_probe_command(
            clip_path, encoded, seg_probe, encoder, crf, preset=preset
        )
        try:
            proc_result = _ffmpeg_run(cmd, timeout=600)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        if proc_result.returncode != 0:
            logger.warning(f"auto-CRF probe encode failed for CRF {crf}, clip {i}")
            continue

        if progress_callback:
            progress_callback(
                f"measuring VMAF for clip {i + 1}/{len(clip_paths)} at CRF {crf}..."
            )

        vmaf_score = _compute_vmaf_score(clip_path, encoded)
        if vmaf_score is not None:
            clip_scores.append(vmaf_score)
            # Only count bytes when VMAF succeeded: clips that encode but
            # fail VMAF are excluded, keeping per-CRF size estimates
            # proportional across CRF values despite uneven clip counts.
            try:
                total_encoded_bytes += encoded.stat().st_size
            except OSError:
                pass

    if not clip_scores:
        return [], None, 0

    return clip_scores, min(clip_scores), total_encoded_bytes


def determine_crf(
    input_path: Path,
    probe: ProbeResult,
    encoder: Encoder,
    target_vmaf: float = _DEFAULT_TARGET_VMAF,
    preset: str = "medium",
    console: Console | None = None,
    output_lines: list[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    on_crf_probe: Callable[[int, float, int], None] | None = None,
    num_clips: int = _NUM_CLIPS,
    clip_duration: float = _CLIP_DURATION,
) -> float:
    """Determine the optimal CRF for a video using VMAF probing.

    Encodes short test clips (from multiple scenes via scene detection) at
    several CRF values, measures VMAF against the original, and fits a curve
    to find the CRF that achieves *target_vmaf*.  The **minimum** VMAF across
    all clips is used for each CRF, ensuring the hardest sampled scene drives
    the recommendation.

    Uses the same preset as the main encode for accurate quality assessment.

    Args:
        input_path: Path to the source video file.
        probe: Already-computed :class:`ProbeResult` for the source.
        encoder: The encoder to use for probe encodes.
        target_vmaf: Desired VMAF score (0-100, default 95).
        preset: x265-style preset for probe encodes (default "medium").
        console: Optional Rich console for progress output.
        num_clips: Number of sample clips to extract (default 3).
        clip_duration: Duration of each sample clip in seconds (default 8).
        on_crf_probe: Optional callback invoked after each successful CRF
            probe with (crf, min_vmaf, total_encoded_bytes).

    Returns:
        Optimal CRF float (0-51).  Falls back to 23.0 on any failure.
    """

    def _out(msg: str) -> None:
        if output_lines is not None:
            output_lines.append(msg)
        elif console:
            console.print(msg)

    _out(f"  [dim]probing CRF with VMAF (target: {target_vmaf})...[/]")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # --- Select and extract clips ---
        if progress_callback:
            progress_callback("detecting scenes...")

        # Warn the user that scdet can take a while on long videos
        if _scdet_available() and probe.duration >= num_clips * clip_duration * 2:
            _out(
                "  [dim]detecting scenes (may take a few minutes for long videos)...[/]"
            )

        start_times = _select_clips(
            input_path, probe.duration, num_clips, clip_duration
        )
        if not start_times:
            _out(
                "  [yellow]warning:[/] could not determine any"
                " suitable clip position, using CRF 23"
            )
            return 23.0

        clip_paths: list[Path] = []
        for i, st in enumerate(start_times):
            clip_path = tmp / f"clip_{i}{input_path.suffix}"
            if not _extract_clip(input_path, st, clip_duration, clip_path):
                _out(
                    "  [yellow]warning:[/] could not extract"
                    f" test clip {i}, using CRF 23"
                )
                logger.warning(f"auto-CRF: clip {i} extraction failed")
                return 23.0
            clip_paths.append(clip_path)

        # --- Probe first clip for its properties ---
        from .probe import probe as _probe_file

        seg_probe = _probe_file(clip_paths[0])
        if seg_probe is None:
            _out("  [yellow]warning:[/] could not probe test clip, using CRF 23")
            return 23.0

        # --- Encode test clips at each candidate CRF ---
        crf_scores: list[tuple[int, float]] = []

        for crf in _CANDIDATE_CRFS:
            clip_scores, min_vmaf, encoded_bytes = _probe_crf(
                crf,
                clip_paths,
                seg_probe,
                encoder,
                tmp,
                preset=preset,
                progress_callback=progress_callback,
            )

            if min_vmaf is None:
                continue

            crf_scores.append((crf, min_vmaf))
            if on_crf_probe is not None:
                on_crf_probe(crf, min_vmaf, encoded_bytes)

            clips_detail = ", ".join(f"{v:.1f}" for v in clip_scores)
            n_failed = len(clip_paths) - len(clip_scores)
            if n_failed:
                clips_detail += f" [yellow]({n_failed} failed)[/]"
            _out(
                f"    testing CRF {crf}..."
                f" min VMAF [green]{min_vmaf:.1f}[/]"
                f"  (clips: {clips_detail})"
            )

            if progress_callback:
                progress_callback(f"CRF {crf}")

            # "Lost cause": if even the best-quality candidate can't
            # hit the target, higher (worse) CRFs will only be worse.
            if crf == _CANDIDATE_CRFS[0] and min_vmaf < target_vmaf:
                _out(
                    "  [yellow]note:[/] video doesn't reach VMAF"
                    f" {target_vmaf} even at CRF {_CANDIDATE_CRFS[0]}"
                    " (best candidate), stopping probe early"
                )
                logger.info(
                    f"auto-crf: lost cause — CRF {_CANDIDATE_CRFS[0]}"
                    f" VMAF {min_vmaf:.1f} < target {target_vmaf}"
                )
                if progress_callback:
                    progress_callback("lost cause")
                break

            # Early stop: once the *minimum* VMAF is below target we have
            # a bracket -- no need to probe higher (worse) CRFs.
            if min_vmaf < target_vmaf and len(crf_scores) >= 2:
                break

        # --- No usable scores -- fall back ---
        if not crf_scores:
            _out("  [yellow]warning:[/] no VMAF scores obtained, using CRF 23")
            logger.warning("auto-CRF: no VMAF scores obtained")
            return 23.0

        # --- Lost cause: all scores below target, skip refinement ---
        if all(vmaf < target_vmaf for _, vmaf in crf_scores):
            # Even the best candidate couldn't reach target. No point
            # probing even better CRFs — return the best we found.
            best = min(crf_scores, key=lambda x: x[0])
            _out(
                f"  best achievable CRF [green]{best[0]}[/]"
                f" (VMAF {best[1]:.1f} < target {target_vmaf})"
            )
            logger.info(
                f"auto-crf: lost cause — returning best CRF {best[0]}"
                f" (VMAF {best[1]:.1f} < target {target_vmaf})"
            )
            return float(best[0])

        # --- Refinement: extend search when all scores are on one side ---
        crf_scores.sort(key=lambda x: x[0])
        refinement_crf: int | None = None
        if all(vmaf >= target_vmaf for _, vmaf in crf_scores):
            refinement_crf = 38
        elif all(vmaf <= target_vmaf for _, vmaf in crf_scores):
            refinement_crf = 13

        if refinement_crf is not None and refinement_crf not in {
            c for c, _ in crf_scores
        }:
            if progress_callback:
                progress_callback(f"refining at CRF {refinement_crf}...")
            clip_scores, min_vmaf, encoded_bytes = _probe_crf(
                refinement_crf,
                clip_paths,
                seg_probe,
                encoder,
                tmp,
                preset=preset,
                progress_callback=progress_callback,
            )
            if min_vmaf is not None:
                crf_scores.append((refinement_crf, min_vmaf))
                if on_crf_probe is not None:
                    on_crf_probe(refinement_crf, min_vmaf, encoded_bytes)
                clips_detail = ", ".join(f"{v:.1f}" for v in clip_scores)
                _out(
                    f"    testing CRF {refinement_crf}..."
                    f" min VMAF [green]{min_vmaf:.1f}[/]"
                    f"  (clips: {clips_detail})"
                )

        # --- Fit and return ---
        optimal_crf = _fit_crf(crf_scores, target_vmaf)

        scores_str = "  ".join(
            f"CRF {crf}: VMAF [green]{v:.1f}[/]" for crf, v in crf_scores
        )
        _out(f"    [dim]{scores_str}[/]")
        _out(f"  [green]selected CRF {optimal_crf}[/] (target VMAF {target_vmaf})")

        logger.info(
            f"auto-crf: target_vmaf={target_vmaf}"
            f" scores={crf_scores}"
            f" selected={optimal_crf}"
        )
        return optimal_crf


def estimate_crf_size_ratio(
    probe_data: list[tuple[int, int]],
    from_crf: float,
    to_crf: float,
) -> float:
    """Estimate the encoded size ratio between two CRF values.

    Uses the empirical relationship between CRF and encoded clip sizes
    from probe data (CRF \u2192 total encoded bytes).  Fits
    log(size) \u2248 a \u00d7 CRF + b and returns the ratio
    *size_at_to_crf* / *size_at_from_crf*.

    A ratio < 1 means *to_crf* produces smaller files (higher CRF).
    Returns 1.0 if insufficient probe data.
    """
    if len(probe_data) < 2:
        return 1.0

    probe_data_sorted = sorted(probe_data, key=lambda x: x[0])

    # Compute per-step size ratio and average
    import math

    n = len(probe_data_sorted)
    sum_x = sum(crf for crf, _ in probe_data_sorted)
    sum_y = sum(math.log(max(s, 1)) for _, s in probe_data_sorted)
    sum_xy = sum(crf * math.log(max(s, 1)) for crf, s in probe_data_sorted)
    sum_xx = sum(crf * crf for crf, _ in probe_data_sorted)

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return 1.0

    slope = (n * sum_xy - sum_x * sum_y) / denom

    # slope is (d log(size) / d CRF). A negative slope means higher CRF = smaller.
    # Ratio = exp(slope * (to_crf - from_crf))
    return math.exp(slope * (to_crf - from_crf))
