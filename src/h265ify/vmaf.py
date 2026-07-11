"""VMAF-based auto-CRF detection for optimal encoding quality.

Uses ffmpeg's libvmaf filter to measure perceptual quality at several CRF
values on a short sample, then fits a curve to find the CRF that achieves a
target VMAF score (default 95, near-transparent quality).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
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

# ── PID tracking for KeyboardInterrupt-safe subprocess cleanup ──
_VMAF_PROCS: dict[int, subprocess.Popen[str]] = {}
_VMAF_PROCS_LOCK = threading.Lock()
_VMAF_ABORTED = (
    threading.Event()
)  # set on KeyboardInterrupt to prevent new subprocesses


def _ffmpeg_run(
    cmd: list[str],
    timeout: float | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Like subprocess.run but tracks PIDs for interrupt-safe cleanup.

    When ``_VMAF_ABORTED`` is set (KeyboardInterrupt), returns a fake
    failure immediately instead of spawning a new subprocess.  This
    prevents worker threads from launching new ffmpeg processes after
    the interrupt handler has already begun cleanup.
    """
    if _VMAF_ABORTED.is_set():
        return subprocess.CompletedProcess(cmd, -1, "", "aborted")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    with _VMAF_PROCS_LOCK:
        _VMAF_PROCS[proc.pid] = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    except BaseException:
        proc.terminate()
        raise
    finally:
        with _VMAF_PROCS_LOCK:
            _VMAF_PROCS.pop(proc.pid, None)


def kill_all_vmaf_procs() -> None:
    """Terminate all tracked VMAF subprocesses (called on KeyboardInterrupt).

    Sets ``_VMAF_ABORTED`` first so any subsequent ``_ffmpeg_run`` calls
    in worker threads short-circuit immediately without spawning new
    subprocesses.
    """
    _VMAF_ABORTED.set()
    with _VMAF_PROCS_LOCK:
        for proc in list(_VMAF_PROCS.values()):
            try:
                proc.terminate()
            except OSError:
                pass
        _VMAF_PROCS.clear()


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


def _extract_segment(input_path: Path, duration: float, output_path: Path) -> bool:
    """Extract a representative segment from the video.

    Samples 60 seconds from the 25% mark (well past opening titles/credits)
    for videos >= 120 seconds.  Shorter videos start from the beginning.
    Uses stream copy so no quality is lost.
    """
    segment_duration = min(60.0, duration) if duration > 0 else 60.0

    # Start at 25% into the video to avoid unrepresentative content like
    # studio logos, title sequences, or end credits.
    if duration >= 120:
        start_time = duration * 0.25
    else:
        start_time = 0.0

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
        str(segment_duration),
        "-c",
        "copy",
        "-map",
        "0:v:0",  # first video stream only; avoid copying extra streams
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

    Video-only encode — no audio, subtitles, hvc1 tags, or faststart.
    Just the bare minimum to measure quality at a given CRF.
    Uses the same preset as the main encode for accurate quality assessment.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
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


def _fit_crf(crf_scores: list[tuple[int, float]], target_vmaf: float) -> int:
    """Find the CRF value that achieves *target_vmaf*.

    Fits a linear regression (VMAF ≈ a × CRF + b) through all measured
    points, then solves for CRF.  Clamped to [0, 51].

    VMAF and CRF have a roughly linear relationship in the useful range
    (CRF 18-35, VMAF ~98-85), so a simple linear fit works well.
    """
    if not crf_scores:
        return 23

    crf_scores.sort(key=lambda x: x[0])
    n = len(crf_scores)

    # All scores above target → use the highest tested CRF (smallest file)
    # since even the worst-quality tested CRF still meets the target quality.
    if all(vmaf >= target_vmaf for _, vmaf in crf_scores):
        return crf_scores[-1][0]

    # All scores below target → use the lowest CRF (best quality)
    # since even the best-quality tested CRF is below the target.
    if all(vmaf <= target_vmaf for _, vmaf in crf_scores):
        return crf_scores[0][0]

    # Simple linear regression: VMAF = a * CRF + b
    sum_x = sum(crf for crf, _ in crf_scores)
    sum_y = sum(vmaf for _, vmaf in crf_scores)
    sum_xy = sum(crf * vmaf for crf, vmaf in crf_scores)
    sum_xx = sum(crf * crf for crf, _ in crf_scores)

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return crf_scores[n // 2][0]

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # Positive slope means VMAF increases with CRF (invalid measurement).
    # Fall back to the median tested CRF.
    if slope >= 0:
        logger.warning(
            f"auto-CRF: unexpected positive slope ({slope:.4f}), using median"
        )
        return crf_scores[n // 2][0]

    # Near-zero slope: all VMAF scores nearly identical across CRFs.
    # The regression is unreliable — fall back to median.
    if abs(slope) < 0.01:
        logger.warning(
            f"auto-CRF: near-zero slope ({slope:.4f}), VMAF scores"
            f" barely change with CRF ({crf_scores}), using median"
        )
        return crf_scores[n // 2][0]

    predicted = (target_vmaf - intercept) / slope
    return max(0, min(51, round(predicted)))


def _try_probe(
    crf_scores: list[tuple[int, float]],
    crf: int,
    segment: Path,
    seg_probe: ProbeResult,
    encoder: Encoder,
    tmp: Path,
    preset: str = "medium",
    console: Console | None = None,
    output_lines: list[str] | None = None,
) -> None:
    """Probe a single CRF value and append to *crf_scores* if successful."""
    if _VMAF_ABORTED.is_set():
        return
    if any(existing == crf for existing, _ in crf_scores):
        return

    encoded = tmp / f"crf_{crf}{segment.suffix}"
    t0 = time.monotonic()

    cmd = _build_probe_command(segment, encoded, seg_probe, encoder, crf, preset=preset)
    try:
        proc_result = _ffmpeg_run(cmd, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        elapsed = time.monotonic() - t0
        if not _VMAF_ABORTED.is_set():
            if output_lines is not None:
                output_lines.append(
                    f"    testing CRF {crf}... [red]{type(e).__name__}[/]"
                )
            elif console:
                console.print(
                    f"    testing CRF [cyan]{crf}[/]\u2026 [red]{type(e).__name__}[/]"
                )
        logger.warning(f"auto-CRF refinement probe failed for CRF {crf}: {e}")
        return
    elapsed = time.monotonic() - t0

    if proc_result.returncode != 0:
        if not _VMAF_ABORTED.is_set():
            if output_lines is not None:
                output_lines.append(f"    testing CRF {crf}... [red]encode failed[/]")
            elif console:
                console.print(
                    f"    testing CRF [cyan]{crf}[/]\u2026 [red]encode failed[/]"
                )
        logger.warning(
            f"auto-CRF refinement probe failed for CRF {crf}:"
            f" {proc_result.stderr[:200]}"
        )
        return

    vmaf_score = _compute_vmaf_score(segment, encoded)

    if vmaf_score is not None:
        crf_scores.append((crf, vmaf_score))
        msg = f"    testing CRF {crf}... VMAF [green]{vmaf_score:.1f}[/]  ({elapsed:.0f}s)"
        if output_lines is not None:
            output_lines.append(msg)
        elif console:
            console.print(msg)
    else:
        if output_lines is not None:
            output_lines.append(f"    testing CRF {crf}... [yellow]VMAF failed[/]")
        elif console:
            console.print(
                f"    testing CRF [cyan]{crf}[/]\u2026 [yellow]VMAF failed[/]"
            )


def determine_crf(
    input_path: Path,
    probe: ProbeResult,
    encoder: Encoder,
    target_vmaf: float = _DEFAULT_TARGET_VMAF,
    preset: str = "medium",
    console: Console | None = None,
    output_lines: list[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> int:
    """Determine the optimal CRF for a video using VMAF probing.

    Encodes short test segments at several CRF values, measures VMAF
    against the original, and fits a curve to find the CRF that achieves
    *target_vmaf*.  Uses the same preset as the main encode for accurate
    quality assessment.

    The probe sample is taken from the 25% mark of the video to avoid
    unrepresentative content (studio logos, title sequences, end credits).

    Args:
        input_path: Path to the source video file.
        probe: Already-computed :class:`ProbeResult` for the source.
        encoder: The encoder to use for probe encodes.
        target_vmaf: Desired VMAF score (0-100, default 95).
        preset: x265-style preset for probe encodes (default "medium").
        console: Optional Rich console for progress output.

    Returns:
        Optimal CRF integer (0-51).  Falls back to 23 on any failure.
    """

    def _out(msg: str) -> None:
        if output_lines is not None:
            output_lines.append(msg)
        elif console:
            console.print(msg)

    _out(f"  [dim]probing CRF with VMAF (target: {target_vmaf})\u2026[/]")
    if progress_callback:
        progress_callback("extracting test segment\u2026")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        segment = tmp / f"segment{input_path.suffix}"

        # --- Extract a representative segment ---
        if not _extract_segment(input_path, probe.duration, segment):
            if not _VMAF_ABORTED.is_set():
                _out(
                    "  [yellow]warning:[/] could not extract test segment, using CRF 23"
                )
                logger.warning("auto-CRF: segment extraction failed")
            return 23

        if _VMAF_ABORTED.is_set():
            return 23

        # Probe the segment for its own properties (pix_fmt, bit depth, etc.)
        from .probe import probe as _probe_file

        seg_probe = _probe_file(segment)
        if seg_probe is None:
            if not _VMAF_ABORTED.is_set():
                _out("  [yellow]warning:[/] could not probe test segment, using CRF 23")
            return 23

        # --- Encode test segments at each candidate CRF ---
        crf_scores: list[tuple[int, float]] = []

        for i, crf in enumerate(_CANDIDATE_CRFS):
            if _VMAF_ABORTED.is_set():
                break

            encoded = tmp / f"crf_{crf}{segment.suffix}"
            t0 = time.monotonic()

            cmd = _build_probe_command(
                segment, encoded, seg_probe, encoder, crf, preset=preset
            )
            if progress_callback:
                progress_callback(f"encoding at CRF {crf}\u2026")
            try:
                proc_result = _ffmpeg_run(cmd, timeout=600)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                elapsed = time.monotonic() - t0
                if not _VMAF_ABORTED.is_set():
                    _out(f"    testing CRF {crf}... [red]{type(e).__name__}[/]")
                logger.warning(f"auto-CRF probe encode failed for CRF {crf}: {e}")
                continue
            elapsed = time.monotonic() - t0

            if proc_result.returncode != 0:
                if _VMAF_ABORTED.is_set():
                    break
                _out(f"    testing CRF {crf}... [red]encode failed[/]")
                logger.warning(
                    f"auto-CRF probe encode failed for CRF {crf}:"
                    f" {proc_result.stderr[:200]}"
                )
                continue

            if progress_callback:
                progress_callback(f"measuring VMAF for CRF {crf}\u2026")
            vmaf_score = _compute_vmaf_score(segment, encoded)

            if vmaf_score is not None:
                crf_scores.append((crf, vmaf_score))
                _out(
                    f"    testing CRF {crf}... "
                    f"VMAF [green]{vmaf_score:.1f}[/]  ({elapsed:.0f}s)"
                )
                if progress_callback:
                    progress_callback(f"CRF {crf}")
                # Early stop: once we cross below the target we have a
                # bracket — no need to probe higher (worse) CRFs.
                if vmaf_score < target_vmaf and len(crf_scores) >= 2:
                    break
            else:
                _out(f"    testing CRF {crf}... [yellow]VMAF failed[/]")

        # --- No usable scores — fall back ---
        if not crf_scores:
            if not _VMAF_ABORTED.is_set():
                _out("  [yellow]warning:[/] no VMAF scores obtained, using CRF 23")
                logger.warning("auto-CRF: no VMAF scores obtained")
            return 23

        # --- Refinement: extend search when all scores are on one side ---
        # After an early stop with a bracket, neither branch triggers (scores
        # span both sides), so refinement only fires for extreme cases.
        crf_scores.sort(key=lambda x: x[0])
        if all(vmaf >= target_vmaf for _, vmaf in crf_scores):
            # All above target — probe a higher CRF
            if progress_callback:
                progress_callback("refining at CRF 38\u2026")
            _try_probe(
                crf_scores,
                38,
                segment,
                seg_probe,
                encoder,
                tmp,
                preset=preset,
                console=console,
                output_lines=output_lines,
            )
        elif all(vmaf <= target_vmaf for _, vmaf in crf_scores):
            # All below target — probe a lower CRF
            if progress_callback:
                progress_callback("refining at CRF 13\u2026")
            _try_probe(
                crf_scores,
                13,
                segment,
                seg_probe,
                encoder,
                tmp,
                preset=preset,
                console=console,
                output_lines=output_lines,
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
