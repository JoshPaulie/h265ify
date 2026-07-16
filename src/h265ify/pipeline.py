"""Pipeline orchestration - file discovery, encoding, progress reporting."""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)
from send2trash import send2trash

from .encoder import build_command, fmt_eta, format_duration, format_size, run_encode

from .hardware import Encoder
from .logger import FFMPEG_LOG_FILE, logger
from .probe import ProbeResult, ffprobe_available, probe

# Canonical video extensions in priority/display order (case-insensitive matching)
# Tuple preserves a stable iteration order; frozenset used for O(1) membership checks.
_VIDEO_EXTENSION_ORDER: tuple[str, ...] = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".wmv",
    ".flv",
    ".m4v",
    ".mts",
    ".m2ts",
    ".ts",
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(_VIDEO_EXTENSION_ORDER)


@dataclass
class EncodeJob:
    """A single file to (potentially) encode."""

    input_path: Path
    probe_result: ProbeResult
    crf: int | None = None  # per-job CRF override (set by --auto)
    display_name: str | None = None  # trimmed name for narrow displays


@dataclass
class EncodeResult:
    """Result of encoding a single file."""

    input_path: Path
    output_path: Path
    success: bool
    elapsed: float  # seconds
    input_size: int
    output_size: int
    skipped: bool = False  # encode succeeded but output was larger than input


def compute_display_names(paths: list[Path]) -> dict[Path, str]:
    """Compute display names with the common prefix trimmed for narrow displays.

    When a batch of files shares a long common prefix (e.g., TV show episodes),
    trimming it produces much shorter names that fit better on narrow terminals:

        The Chair Company - S01E01 - Pilot.mkv  →  01 - Pilot.mkv
        The Chair Company - S01E02 - Episode 2.mkv  →  02 - Episode 2.mkv
        The Chair Company - S01E03 - Conclusion.mkv  →  03 - Conclusion.mkv

    Files that don't share a meaningful prefix retain their original names.
    A single file always keeps its full name.
    """
    if len(paths) <= 1:
        return {p: p.name for p in paths}

    names = [p.name for p in paths]
    prefix = os.path.commonprefix(names)

    # Only trim if the prefix is long enough to be meaningful
    # (at least 8 chars and at least 25% of average name length)
    avg_len = sum(len(n) for n in names) / len(names)
    if len(prefix) < 8 or len(prefix) / avg_len < 0.25:
        return {p: p.name for p in paths}

    # Check all trimmed results are reasonable before committing
    result: dict[Path, str] = {}
    all_ok = True
    for p in paths:
        display = p.name.removeprefix(prefix)
        if not display or display == p.suffix or len(display) < 5:
            all_ok = False
            break
        result[p] = display

    if not all_ok:
        return {p: p.name for p in paths}
    return result


def _is_video_file(p: Path) -> bool:
    """
    Return True if *p* is a video file that should be queued for encoding.

    Note: Files already h265 by codec are caught later by :func:`prepare_jobs`.
    """
    return (
        p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and not p.stem.endswith("_h265")
    )


def _iter_files(paths: list[Path], console: Console | None = None) -> Iterator[Path]:
    """Yield all files under the given paths (files or recursive directories)."""
    seen: set[Path] = set()
    for p in paths:
        p = p.resolve()
        if p in seen:
            continue
        seen.add(p)

        if p.is_file():
            yield p
        elif p.is_dir():
            for ext in _VIDEO_EXTENSION_ORDER:
                for fpath in sorted(p.rglob(f"*{ext}")):
                    if fpath not in seen:
                        seen.add(fpath)
                        yield fpath
        else:
            if console:
                console.print(f"  [yellow]warning:[/] {p} does not exist, skipping")
            else:
                logger.warning(f"{p} does not exist, skipping")


def find_video_files(paths: list[Path], console: Console | None = None) -> list[Path]:
    """Given a list of paths (files or directories), return all video files.

    Directories are walked recursively. Non-video files are silently skipped.
    """
    return [f for f in _iter_files(paths, console) if _is_video_file(f)]


def probe_files(
    files: list[Path],
    console: Console,
) -> list[ProbeResult]:
    """Probe all candidate files with ffprobe, showing a progress bar.

    Uses a thread pool (default: ``os.cpu_count()``) to probe files in
    parallel.  Each ffprobe call is I/O-bound, so threading yields a
    near-linear speedup on large batches.

    Set ``H265IFY_PROBE_THREADS`` to override the thread count.
    """
    if not ffprobe_available():
        console.print("[red]error:[/] ffprobe not found.")
        return []

    results: list[ProbeResult] = []
    total = len(files)

    # Respect H265IFY_PROBE_THREADS env var for advanced tuning.
    # Named verbosely to avoid confusion with encoding parallelism.
    env_threads = os.environ.get("H265IFY_PROBE_THREADS")
    if env_threads is not None:
        try:
            workers = int(env_threads)
        except ValueError:
            console.print(
                f"  [yellow]warning:[/] H265IFY_PROBE_THREADS='{env_threads}'"
                " is not an integer, using default"
            )
            workers = os.cpu_count() or 4
    else:
        workers = os.cpu_count() or 4

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style="green", style="grey42"),
        TaskProgressColumn(text_format="{task.percentage:>3.0f}%"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(
            f"probing {total} file(s) [{workers} threads]…", total=total
        )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            fut_to_path = {executor.submit(probe, f): f for f in files}
            for future in concurrent.futures.as_completed(fut_to_path):
                result = future.result()
                if result is not None:
                    results.append(result)
                else:
                    f = fut_to_path[future]
                    console.print(
                        f"  [yellow]warning:[/] could not probe {f.name}, skipping"
                    )
                progress.advance(task)
        except KeyboardInterrupt:
            console.print("\n  [yellow]probing interrupted[/]")
            return results
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    return results


def prepare_jobs(
    results: list[ProbeResult],
    replace: bool,
    skip_existing: bool = True,
    output_format: str | None = None,
) -> tuple[list[EncodeJob], list[ProbeResult]]:
    """Filter probe results and build the list of encode jobs.

    `replace` corresponds to `--yolo` (in-place encoding), **not** the
    `--replace` batch-swap mode.  Set `skip_existing=False` to queue
    files even when the output already exists.

    Returns `(jobs_to_encode, skipped_probes)`.
    """
    jobs: list[EncodeJob] = []
    skipped: list[ProbeResult] = []

    for r in results:
        if r.is_h265:
            logger.info(f"skip {r.path.name}: already h265")
            skipped.append(r)
            continue

        output = get_output_path(r.path, replace, output_format)
        if skip_existing and output.exists() and not replace:
            logger.info(f"skip {r.path.name}: output exists ({output.name})")
            skipped.append(r)
            continue

        jobs.append(EncodeJob(input_path=r.path, probe_result=r))

    return jobs, skipped


def get_output_path(
    input_path: Path,
    replace: bool,
    output_format: str | None = None,
) -> Path:
    """Determine the output path for an encode job.

    In suffix mode (`replace=False`): appends `_h265` to the stem,
    e.g. `video.mkv` → `video_h265.mkv`.

    In in-place mode (`replace=True`, i.e. `--yolo`): keeps the same stem
    and directory, converting the extension only when the source container is
    non-standard (e.g. `.webm` → `.mp4`).

    `output_format` overrides the container for either mode. Non-standard
    containers (anything other than mp4/mkv/mov) are normalized to mp4.
    """
    suffix = output_format if output_format else input_path.suffix.lstrip(".")
    # Normalize non-standard containers to mp4
    if suffix not in ("mp4", "mkv", "mov"):
        suffix = "mp4"
    ext = f".{suffix}"

    if replace:
        # In-place mode: same stem, (possibly normalized) extension
        return input_path.with_suffix(ext)
    # Suffix mode: video.mkv → video_h265.mkv
    return input_path.with_stem(input_path.stem + "_h265").with_suffix(ext)


def _delete_user_file(path: Path, *, permanent: bool) -> None:
    """Delete a user file, sending to trash by default or permanently."""
    if permanent:
        path.unlink(missing_ok=True)
    else:
        send2trash(str(path))


def _tmp_path(output: Path) -> Path:
    """Return the temp path used for atomic encode-and-replace.

    Encodes write here first; on success the file is renamed to the real
    output so a failed or interrupted encode never leaves a partial file in
    place.  Example: `video_h265.mp4` → `video_h265.h265-tmp.mp4`.
    """
    return output.with_name(f"{output.stem}.h265-tmp{output.suffix}")


def run_pipeline(
    jobs: list[EncodeJob],
    encoder: Encoder,
    crf: int,
    replace: bool,
    dry_run: bool,
    console: Console,
    output_format: str | None = None,
    permanent: bool = False,
    preset: str | None = None,
    resize: str | None = None,
    no_upscale: bool = False,
    reencode_audio: bool = False,
    halt_on_increase: bool = False,
    on_job_complete: Callable[[EncodeJob, EncodeResult], None] | None = None,
) -> tuple[list[EncodeResult], bool]:
    """Run the encoding pipeline on all jobs. All encodes run sequentially.

    Pass dry_run=True to preview without encoding. Stops on first failure.
    When *halt_on_increase* is True, also stops if any output is larger
    than its input.
    Returns (results, interrupted).
    """
    # --- Dry run: preview what would happen ---
    if dry_run:
        results: list[EncodeResult] = []
        for job in jobs:
            output = get_output_path(job.input_path, replace, output_format)
            input_size = job.probe_result.file_size
            size_str = f" ({format_size(input_size)})" if input_size > 0 else ""
            _name = job.display_name or job.input_path.name
            console.print(f"  {_name}{size_str}")
            logger.info(f"dry-run: {job.input_path.name} → {output.name}")
            results.append(
                EncodeResult(
                    input_path=job.input_path,
                    output_path=output,
                    success=True,
                    elapsed=0,
                    input_size=job.probe_result.file_size,
                    output_size=job.probe_result.file_size,
                )
            )
        return results, False

    # --- Sequential encoding ---
    results = []
    total = len(jobs)
    overall_label = f"encoding {total} file(s)"
    total_duration = sum(job.probe_result.duration for job in jobs)

    # Track temp files for SIGINT cleanup
    _current_tmp: Path | None = None

    def _cleanup() -> None:
        if _current_tmp is not None and _current_tmp.exists():
            _current_tmp.unlink(missing_ok=True)

    def _sigint_handler(signum: int, frame: object) -> None:
        _cleanup()
        raise KeyboardInterrupt()

    orig_handler = signal.signal(signal.SIGINT, _sigint_handler)
    interrupted = False

    try:
        with Progress(
            TextColumn("{task.description}"),
            BarColumn(complete_style="green", finished_style="green", style="grey42"),
            TaskProgressColumn(text_format="{task.percentage:>5.1f}%"),
            TextColumn("{task.fields[suffix]}"),
            console=console,
            transient=False,
        ) as progress:
            overall_total = total_duration if total_duration > 0 else float(total)
            overall = progress.add_task(
                overall_label, total=overall_total, suffix=f"0/{total}"
            )
            completed_duration = 0.0
            current_file: TaskID | None = None

            for i, job in enumerate(jobs, 1):
                output = get_output_path(job.input_path, replace, output_format)
                tmp_output = _tmp_path(output)
                encode_target = tmp_output
                _current_tmp = tmp_output

                # Set up current file progress bar
                if current_file is not None:
                    progress.remove_task(current_file)
                current_file = progress.add_task(
                    f"  {job.display_name or job.input_path.name}",
                    total=100,
                    suffix="starting…",
                )

                logger.info(
                    f"encoding: {job.input_path.name} → {output.name}"
                    f"  ({format_size(job.probe_result.file_size)})"
                )

                # _on_progress closes over current_file and completed_duration
                # from this scope. run_encode is synchronous, so both values
                # are stable for the entire duration of this iteration.
                def _on_progress(
                    pct: float, speed: float, current_seconds: float
                ) -> None:
                    if current_file is not None:
                        # Speed display
                        speed_str = f"@ {speed:.1f}x" if speed > 0 else "initializing…"
                        # ETA
                        if speed > 0 and job.probe_result.duration > 0:
                            file_remaining = max(
                                0.0, job.probe_result.duration - current_seconds
                            )
                            eta_str = f"  eta {fmt_eta(file_remaining / speed)}"
                        else:
                            eta_str = ""
                        # Elapsed / total time (always shown so user sees movement)
                        if job.probe_result.duration > 0:
                            current_str = fmt_eta(current_seconds)
                            total_str = fmt_eta(job.probe_result.duration)
                            time_str = f"  {current_str}/{total_str}"
                        else:
                            time_str = f"  {fmt_eta(current_seconds)}"
                        progress.update(
                            current_file,
                            completed=pct,
                            suffix=f"{time_str}  {speed_str}{eta_str}",
                        )
                    if total_duration > 0:
                        progress.update(
                            overall, completed=completed_duration + current_seconds
                        )
                        if speed > 0:
                            overall_remaining = max(
                                0.0,
                                total_duration - completed_duration - current_seconds,
                            )
                            progress.update(
                                overall,
                                suffix=f"{i}/{total}  eta {fmt_eta(overall_remaining / speed)}",
                            )

                cmd_warnings: list[str] = []
                cmd = build_command(
                    job.input_path,
                    encode_target,
                    job.probe_result,
                    encoder,
                    job.crf if job.crf is not None else crf,
                    output_format,
                    reencode_audio=reencode_audio,
                    preset=preset,
                    resize=resize,
                    no_upscale=no_upscale,
                    warnings=cmd_warnings,
                )

                for w in cmd_warnings:
                    console.print(w)

                # ── Encoding with auto-retry on crash ──
                max_retries = 2  # total attempts = max_retries + 1
                success = False
                errors: list[str] = []
                elapsed = 0.0

                for attempt in range(1, max_retries + 2):
                    if attempt > 1:
                        # Exponential backoff: 1s, 2s
                        backoff = 2 ** (attempt - 2)
                        logger.warning(
                            f"retry {attempt}/{max_retries + 1}:"
                            f" {job.input_path.name} (waiting {backoff}s)"
                        )
                        time.sleep(backoff)
                        if tmp_output.exists():
                            tmp_output.unlink(missing_ok=True)
                        _current_tmp = tmp_output

                    t0 = time.monotonic()

                    # Early-abort hook: poll the temp file size once per second.
                    # If it ever exceeds the original, kill ffmpeg to save cycles.
                    def _should_cancel() -> bool:
                        try:
                            return (
                                tmp_output.stat().st_size > job.probe_result.file_size
                            )
                        except OSError:
                            return False

                    success, errors = run_encode(
                        cmd,
                        duration=job.probe_result.duration,
                        progress_callback=_on_progress,
                        cancel_check=_should_cancel
                        if job.probe_result.file_size > 0
                        else None,
                    )

                    for e in errors:
                        console.print(e)

                    elapsed = time.monotonic() - t0

                    if success:
                        break  # encoded OK — proceed to size check

                    # Crash failure — retry unless we've exhausted attempts
                    if attempt < max_retries + 1:
                        logger.warning(
                            f"attempt {attempt}/{max_retries + 1} failed for"
                            f" {job.input_path.name}, retrying"
                        )
                        if tmp_output.exists():
                            tmp_output.unlink(missing_ok=True)
                        _current_tmp = None
                    else:
                        logger.error(
                            f"failed:   {job.input_path.name}"
                            f"  ({max_retries + 1} attempts)"
                        )

                # Post-encode: check output size on the temp file before
                # moving it into place.  This is important for --yolo mode:
                # we must not overwrite the original if the encode grew.
                output_size = 0
                skipped_larger = False
                if success:
                    output_size = tmp_output.stat().st_size
                    if (
                        job.probe_result.file_size > 0
                        and output_size > job.probe_result.file_size
                    ):
                        # Output is larger than input — abort this file.
                        pct = (output_size / job.probe_result.file_size - 1) * 100
                        logger.warning(
                            f"skipped:  {job.input_path.name}  output larger"
                            f"  {format_size(job.probe_result.file_size)}"
                            f" → {format_size(output_size)}"
                            f"  +{pct:.1f}%"
                        )
                        tmp_output.unlink(missing_ok=True)
                        _current_tmp = None
                        skipped_larger = True
                    else:
                        # Output is smaller (or input size unknown) — keep it.
                        if replace:
                            # In --yolo mode: trash/delete the original, then
                            # swap the encoded file into its place.
                            _delete_user_file(job.input_path, permanent=permanent)
                            logger.info(f"deleted original: {job.input_path.name}")
                        try:
                            os.replace(tmp_output, output)
                            _current_tmp = None  # swapped, no longer a temp file
                        except OSError as e:
                            console.print(
                                f"  [red]error:[/] could not replace {job.input_path.name}: {e}"
                            )
                            success = False
                            if tmp_output.exists():
                                tmp_output.unlink(missing_ok=True)
                            _current_tmp = None
                        if success:
                            if job.probe_result.file_size > 0:
                                pct = (
                                    1 - output_size / job.probe_result.file_size
                                ) * 100
                                logger.info(
                                    f"encoded:  {job.input_path.name}"
                                    f"  {format_size(job.probe_result.file_size)}"
                                    f" → {format_size(output_size)}"
                                    f"  {pct:+.1f}%"
                                    f"  {format_duration(elapsed)}"
                                )
                            else:
                                logger.info(
                                    f"encoded:  {job.input_path.name}  {format_duration(elapsed)}"
                                )
                else:
                    # Clean up temp on failure (retry loop already logged)
                    if tmp_output.exists():
                        tmp_output.unlink(missing_ok=True)
                    _current_tmp = None

                result = EncodeResult(
                    input_path=job.input_path,
                    output_path=output,
                    success=success,
                    elapsed=elapsed,
                    input_size=job.probe_result.file_size,
                    output_size=output_size,
                    skipped=skipped_larger,
                )

                completed_duration += job.probe_result.duration

                # Advance overall progress
                if total_duration > 0:
                    progress.update(
                        overall,
                        completed=completed_duration,
                        suffix=f"{i}/{total}",
                    )
                else:
                    progress.update(
                        overall,
                        advance=1,
                        suffix=f"{i}/{total}",
                    )

                if on_job_complete:
                    on_job_complete(job, result)

                results.append(result)

                # Log failure and continue to next file
                if not success:
                    if total_duration == 0:
                        progress.update(overall, advance=1)

                # Halt batch on size increase if requested
                if skipped_larger and halt_on_increase:
                    console.print(
                        "\n  [yellow]halting batch[/] — output grew larger than input"
                    )
                    if total_duration == 0:
                        for _ in range(i, total):
                            progress.update(overall, advance=1)
                    break

            # Clean up the last file's progress bar
            if current_file is not None:
                progress.remove_task(current_file)
                current_file = None

    except KeyboardInterrupt:
        interrupted = True
        console.print("\n  [yellow]interrupted[/]")
    finally:
        signal.signal(signal.SIGINT, orig_handler)

    return results, interrupted


# --- Replace mode ---

_H265_SUFFIX = "_h265"


@dataclass
class ReplacePair:
    """A matched pair of _h265 file and its original."""

    h265_path: Path
    original_path: Path


def find_replace_pairs(paths: list[Path], console: Console) -> list[ReplacePair]:
    """Given paths (files/directories), find all _h265 → original pairs.

    For each *_h265.* file found, strips the _h265 suffix and looks for
    the original file with any video extension in the same directory.
    """
    pairs: list[ReplacePair] = []
    seen_originals: set[Path] = set()

    # Collect all files from the given paths
    all_files = list(_iter_files(paths))

    # Identify _h265 files; the stem must *end* with _h265, not merely contain it.
    # (e.g. "my_h265_video.mp4" is not a match, "my_video_h265.mp4" is.)
    h265_files = [
        f
        for f in all_files
        if f.suffix.lower() in VIDEO_EXTENSIONS and f.stem.endswith(_H265_SUFFIX)
    ]

    # For each _h265 file, find the original
    for hf in h265_files:
        # Strip _h265 from stem to get original stem
        original_stem = hf.stem[: -len(_H265_SUFFIX)]
        if not original_stem:
            continue  # file named just "_h265.ext" - skip

        parent = hf.parent
        original: Path | None = None

        # Look for a file with the original stem + any video extension
        for ext in _VIDEO_EXTENSION_ORDER:
            candidate = parent / f"{original_stem}{ext}"
            if candidate.exists() and candidate not in seen_originals:
                original = candidate
                break

        if original is not None:
            seen_originals.add(original)
            pairs.append(ReplacePair(h265_path=hf, original_path=original))
        else:
            console.print(
                f"  [yellow]warning:[/] no original found for {hf.name}, skipping"
            )

    return pairs


def run_replace(
    pairs: list[ReplacePair],
    console: Console,
    dry_run: bool = False,
    permanent: bool = False,
    display_names: dict[Path, str] | None = None,
) -> tuple[int, int]:
    """Replace original files with their _h265 counterparts.

    For each pair: delete the original, rename the _h265 file to use
    the original's stem + the _h265 file's extension.

    Returns (replaced_count, skipped_count).
    """
    replaced = 0
    skipped = 0

    for pair in pairs:
        # New name: original stem + _h265 file's extension
        new_path = pair.original_path.with_suffix(pair.h265_path.suffix)

        _orig = (display_names or {}).get(pair.original_path, pair.original_path.name)
        _h265 = (display_names or {}).get(pair.h265_path, pair.h265_path.name)
        console.print(f"  {_orig}  ←  {_h265}")
        if dry_run:
            logger.info(
                f"dry-run replace: {pair.original_path.name} ← {pair.h265_path.name}"
            )
            replaced += 1
            continue

        try:
            # Delete original (trash by default, permanent with --permanent)
            _delete_user_file(pair.original_path, permanent=permanent)
            # Rename _h265 to new name
            pair.h265_path.rename(new_path)
            logger.info(
                f"replaced: {pair.original_path.name} ← {pair.h265_path.name} → {new_path.name}"
            )
            replaced += 1
        except OSError as e:
            console.print(f"  [red]error:[/] {e}")
            logger.error(f"replace failed: {pair.original_path.name}: {e}")
            skipped += 1

    return replaced, skipped


# ── Disk space check ──

# Both thresholds are 0.5 but applied against different bases
# (total_input vs max_file), kept separate for independent tuning.
_DISK_CHECK_THRESHOLD_NORMAL: float = 0.5
"""
Fraction of total input size used as the free-space floor for normal mode.

We assume typical h265 output is ~40-60% of the original.  Since originals
are retained alongside outputs during review, we need enough free space to
accommodate all outputs.  This threshold is conservative: 0.5 means "free
space must be at least 50% of total input size" (i.e. we assume outputs
will be at most 50% of inputs, which holds for most content).
"""

_DISK_CHECK_THRESHOLD_YOLO: float = 0.5
"""
Fraction of the *largest single file* used as the free-space floor for
``--yolo`` mode.  Files are processed sequentially and the original is
deleted after encoding, so we only need headroom for one temp file at a
time.  0.5 is conservative: temp file ≈ output ≈ 50% of the original.
"""


def check_disk_space(
    jobs: list[EncodeJob],
    *,
    yolo: bool = False,
    output_format: str | None = None,
    console: Console,
) -> bool:
    """Check if there's enough free disk space for the encoding session.

    Examines the filesystem where the first output file would be written.
    All outputs from a single session are assumed to be on the same
    filesystem (current behaviour: same directory as inputs).

    Returns ``True`` if:

    * No jobs to check.
    * Free space is above the applicable threshold.
    * Can't determine free space (unlikely, don't block).

    Returns ``False`` if space is clearly insufficient, after printing an
    error message with the advisory threshold and a pointer to
    ``--ignore-full-disk``.
    """
    if not jobs:
        return True

    # Determine the output directory for the first job.
    first_out = get_output_path(jobs[0].input_path, replace=yolo, output_format=output_format)
    parent = first_out.parent

    try:
        usage = shutil.disk_usage(parent)
    except OSError:
        logger.warning("could not determine disk usage for %s, skipping check", parent)
        return True

    free = usage.free

    total_input = sum(j.probe_result.file_size for j in jobs)
    max_file = max(j.probe_result.file_size for j in jobs)

    if yolo:
        # In --yolo mode each file's original is deleted right after
        # encoding, so we only need room for one temp file at a time.
        needed = int(max_file * _DISK_CHECK_THRESHOLD_YOLO)
        mode_desc = "in-place replacement (--yolo)"
    else:
        # Normal mode: originals + outputs coexist during review.
        # We need enough free space to accommodate all outputs.
        needed = int(total_input * _DISK_CHECK_THRESHOLD_NORMAL)
        mode_desc = "normal mode (originals kept)"

    if free < needed:
        console.print()
        console.print(
            f"[red]error:[/] not enough free disk space for {mode_desc}.\n"
            f"  free:       [bold]{format_size(free)}[/]\n"
            f"  estimated:  {format_size(needed)} (based on {len(jobs)} file(s), "
            f"{format_size(total_input)} total)\n"
            f"  Use [bold]--ignore-full-disk[/] to bypass this check."
        )
        return False

    return True


def print_summary(
    results: list[EncodeResult],
    skipped: list[ProbeResult],
    dry_run: bool,
    console: Console,
) -> None:
    """Print a final summary of the encoding run."""
    console.print()

    h265_count = sum(1 for r in skipped if r.is_h265)
    exists_count = len(skipped) - h265_count

    if h265_count > 0:
        console.print(
            f"  skipped {h265_count} already-h265 file{'s' if h265_count != 1 else ''}"
        )
    if exists_count > 0:
        console.print(
            f"  skipped {exists_count} file{'s' if exists_count != 1 else ''} (output exists)"
        )

    total_attempted = len(results)
    succeeded = sum(1 for r in results if r.success and not r.skipped)
    skipped_larger = sum(1 for r in results if r.skipped)
    failed = total_attempted - succeeded - skipped_larger

    if dry_run:
        n = total_attempted
        total_size = sum(r.input_size for r in results)
        size_str = f"  ({format_size(total_size)})" if total_size > 0 else ""
        console.print(f"  {n} file{'s' if n != 1 else ''} would be encoded{size_str}")
        return

    if total_attempted == 0:
        console.print("  0 encoded")
        return

    total_in = 0
    total_out = 0
    total_time = 0.0

    for r in results:
        total_time += r.elapsed
        if not r.skipped:
            total_in += r.input_size
            if r.success and r.output_size > 0:
                total_out += r.output_size

    if total_in > 0 and total_out > 0:
        pct = (1 - total_out / total_in) * 100
        if pct > 0:
            delta = f"[green]-{pct:.1f}%[/]"
        elif pct < -0.05:
            delta = f"[red]+{-pct:.1f}%[/]"
        else:
            delta = "~0%"
        console.print(
            f"  [bold]{succeeded} encoded[/], "
            f"{format_size(total_in)} → {format_size(total_out)}  "
            f"{delta}"
        )
        logger.info(
            f"session: {succeeded} encoded, {failed} failed, {len(skipped)} skipped"
            f"  {format_size(total_in)} → {format_size(total_out)}  {pct:+.1f}%"
            f"  total {format_duration(total_time)}"
        )
    elif succeeded > 0:
        console.print(f"  [bold]{succeeded} encoded[/]")
        logger.info(
            f"session: {succeeded} encoded, {failed} failed, {len(skipped)} skipped"
            f"  total {format_duration(total_time)}"
        )

    if skipped_larger > 0:
        console.print(
            f"  [yellow]{skipped_larger} skipped[/] (output larger than input)"
        )
    if failed > 0:
        console.print(f"  [red]{failed} failed[/]")
        console.print(f"  [dim]see {FFMPEG_LOG_FILE} for full ffmpeg output[/]")

    console.print(f"  total  {format_duration(total_time)}")
