"""h265ify - a zero-fuss h265/HEVC video encoder wrapper for ffmpeg."""

from __future__ import annotations

import argparse
import platform
import random
import re
import sys
from importlib.metadata import version
from pathlib import Path


from rich.console import Console

from .encoder import format_size
from .hardware import Encoder, detect_encoder
from .logger import (
    ERROR_LOG_FILE,
    FFMPEG_LOG_FILE,
    LOG_FILE,
    logger,
    generate_tag,
    get_session_tag,
    install_excepthook,
    set_session_tag,
)
from .pipeline import (
    EncodeJob,
    EncodeResult,
    compute_display_names,
    find_replace_pairs,
    find_video_files,
    get_output_path,
    prepare_jobs,
    print_summary,
    probe_files,
    run_pipeline,
    run_replace,
)
from .vmaf import _CLIP_DURATION, _NUM_CLIPS, determine_crf, vmaf_available


def _positive_int_ge(value: str, minimum: int = 1) -> int:
    """Argparse type helper: validate int >= *minimum*."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer") from exc
    if n < minimum:
        raise argparse.ArgumentTypeError(f"'{n}' must be at least {minimum}")
    return n


def _positive_float_gt(value: str, minimum: float = 0.0) -> float:
    """Argparse type helper: validate float > *minimum*."""
    try:
        n = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid number") from exc
    if n <= minimum:
        raise argparse.ArgumentTypeError(f"'{n}' must be greater than {minimum}")
    return n


def _valid_resize(spec: str) -> bool:
    """Return True if *spec* is a recognised resize value."""
    lowered = spec.lower().strip()
    if lowered in ("720p", "1080p", "4k"):
        return True
    if "x" in lowered:
        try:
            w, h = lowered.split("x", 1)
            int(w)
            int(h)
            return True
        except ValueError:
            return False
    try:
        int(lowered)
        return True
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="h265ify",
        description='"Zero-fuss" bulk h265/HEVC re-encoding (yet another ffmpeg wrapper!)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  h265ify video.mkv                re-encode to video_h265.mkv
  h265ify ~/Videos/                re-encode all videos in directory (recursive)
  h265ify --crf 20 video.mkv       higher quality (lower CRF = better)
  h265ify --resize 720p video.mkv  shrink to 720p, preserving aspect ratio
  h265ify --yolo video.mp4         re-encode and replace original immediately
  h265ify --replace ~/Videos/      replace originals with _h265 copies (no encoding)
  h265ify --dry-run ~/Movies/      preview what would be encoded
  h265ify --preset fast video.mkv  faster encoding, slightly larger file
  h265ify --cpu video.mkv          force software encoding (libx265)
  h265ify --vmaf 95 video.mkv      evaluate and recommend optimal CRF using VMAF (no encode)
  h265ify --vmaf 93 ~/Videos/      evaluate all videos with custom VMAF target
  h265ify --vmaf 95 --sample 5     evaluate a random sample of 5 files
  h265ify --vmaf 95 --sample 25%   evaluate a random 25%% of files
  h265ify --vmaf 95 --vmaf-clips 5 --vmaf-clip-duration 10  customise VMAF sampling (5 clips, 10s each)
""",
    )

    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="video files or directories to process",
    )

    # --- Encoding options ---
    encoding = parser.add_argument_group("encoding options")
    encoding.add_argument(
        "--crf",
        type=int,
        default=None,
        metavar="N",
        help="quality: 0–51, lower = better (default: 23). "
        "For hardware encoders, mapped to the closest equivalent. "
        "Mutually exclusive with --vmaf.",
    )
    encoding.add_argument(
        "--vmaf",
        nargs="?",
        const=95.0,
        type=float,
        default=None,
        metavar="VMAF",
        help="evaluate and recommend optimal CRF using VMAF perceptual quality "
        "metric (no encoding). Probes each file at several CRF values, measures "
        "VMAF, and reports the CRF that achieves the target score (default: 95). "
        "Mutually exclusive with --crf and other encoding options.",
    )
    encoding.add_argument(
        "--sample",
        type=str,
        metavar="N|N%",
        help="randomly sample N files or N%% for --vmaf evaluation",
    )
    encoding.add_argument(
        "--vmaf-clips",
        type=lambda v: _positive_int_ge(v, 1),
        default=3,
        metavar="N",
        help="number of sample clips for VMAF evaluation (default: 3)",
    )
    encoding.add_argument(
        "--vmaf-clip-duration",
        type=lambda v: _positive_float_gt(v, 0.0),
        default=8.0,
        metavar="SECS",
        help="duration of each VMAF sample clip in seconds (default: 8)",
    )
    encoding.add_argument(
        "--resize",
        "-r",
        metavar="SPEC",
        help="resize output: '720p', '1080p', '4k', or '1280x720'",
    )
    encoding.add_argument(
        "--format",
        dest="output_format",
        choices=["mp4", "mkv", "mov"],
        help="force output container: mp4, mkv, or mov "
        "(default: preserve mp4/mkv/mov; convert everything else to mp4)",
    )

    # --- Output options ---
    output_grp = parser.add_argument_group("output options")
    output_grp.add_argument(
        "--yolo",
        "-y",
        action="store_true",
        help="replace original file immediately after encoding (risky!)",
    )
    output_grp.add_argument(
        "--replace",
        action="store_true",
        help="replace originals with existing '_h265' copies (no encoding)",
    )
    output_grp.add_argument(
        "--dry-run",
        "--noop",
        action="store_true",
        help="show what would happen without encoding or replacing anything",
    )
    output_grp.add_argument(
        "-P",
        "--perm",
        "--permanent",
        dest="permanent",
        action="store_true",
        help="permanently delete replaced originals instead of sending to trash",
    )

    # --- Advanced options ---
    advanced = parser.add_argument_group("advanced options")
    advanced.add_argument(
        "--preset",
        choices=[
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        ],
        default="medium",
        help="encoding speed/efficiency preset (default: medium). "
        "Mapped to hardware encoder equivalents where applicable.",
    )
    advanced.add_argument(
        "--cpu",
        action="store_true",
        help="use CPU encoding (libx265) instead of hardware acceleration",
    )
    advanced.add_argument(
        "--reencode-audio",
        action="store_true",
        help="re-encode audio (AAC for MP4, Opus for MKV) instead of stream-copy",
    )
    advanced.add_argument(
        "--no-upscale",
        action="store_true",
        help="with --resize: skip if input is already ≤ target resolution",
    )
    advanced.add_argument(
        "--halt-on-increase",
        "-H",
        action="store_true",
        help="stop the entire batch if any file comes out larger than the original",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"h265ify {version('h265ify')}",
        help="print version and exit",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="write a diagnostic report with recent logs to a file",
    )

    args = parser.parse_args()

    # --- report mode (must run alone) ---
    if args.report:
        _cmd_report(args)
        return

    if not args.paths:
        parser.print_usage()
        sys.exit(1)

    console = Console(highlight=False)
    err_console = Console(stderr=True, highlight=False)

    # --- Install exception hook ---
    install_excepthook()

    # --- Generate session tag ---
    _tag = generate_tag()
    set_session_tag(_tag)

    # --- Log session start ---
    _ver = version("h265ify")
    logger.info(f"=== h265ify {_ver}  [{_tag}] ===")
    mode = (
        "vmaf-eval"
        if args.vmaf
        else ("dry-run" if args.dry_run else ("replace" if args.replace else "encode"))
    )
    logger.info(f"mode={mode}  paths={', '.join(str(p) for p in args.paths)}")
    logger.info(f"log: {LOG_FILE}")

    try:
        _run(args, console, err_console)
    except KeyboardInterrupt:
        console.print("\n  [yellow]interrupted[/]")
        sys.exit(130)


def _run(args: argparse.Namespace, console: Console, err_console: Console) -> None:
    """Dispatch to replace or encode mode after validation."""
    # --- Mutual exclusivity ---
    if args.replace and args.yolo:
        err_console.print("[red]error:[/] --replace and --yolo are mutually exclusive.")
        sys.exit(1)

    if args.vmaf is not None and args.replace:
        err_console.print("[red]error:[/] --vmaf and --replace are mutually exclusive.")
        sys.exit(1)

    if args.vmaf is not None and args.yolo:
        err_console.print("[red]error:[/] --vmaf and --yolo are mutually exclusive.")
        sys.exit(1)

    # --- --permanent requires --yolo or --replace ---
    if args.permanent and not args.replace and not args.yolo:
        err_console.print(
            "[red]error:[/] --permanent has no effect without --yolo or --replace"
        )
        sys.exit(1)

    # --- --permanent with --dry-run is nonsensical ---
    if args.permanent and args.dry_run:
        err_console.print(
            "[red]error:[/] --permanent has no effect with --dry-run (nothing is changed)"
        )
        sys.exit(1)

    # --- --sample without --vmaf is a no-op ---
    if args.sample is not None and args.vmaf is None:
        err_console.print("[yellow]warning:[/] --sample has no effect without --vmaf")

    # --- VMAF evaluation mode ---
    if args.vmaf is not None:
        # ── Mutually exclusive with encoding flags ──
        encoding_conflicts: list[str] = []
        if args.crf is not None:
            encoding_conflicts.append("--crf")
        if args.resize:
            encoding_conflicts.append("--resize")
        if args.reencode_audio:
            encoding_conflicts.append("--reencode-audio")
        if args.output_format:
            encoding_conflicts.append("--format")
        if args.halt_on_increase:
            encoding_conflicts.append("--halt-on-increase")
        if args.no_upscale:
            encoding_conflicts.append("--no-upscale")
        if encoding_conflicts:
            err_console.print(
                f"[red]error:[/] --vmaf is mutually exclusive with encoding flags: "
                f"{', '.join(encoding_conflicts)}"
            )
            sys.exit(1)

        # Validate --vmaf value
        if not (0 <= args.vmaf <= 100):
            err_console.print("[red]error:[/] --vmaf must be between 0 and 100")
            sys.exit(1)

        if not vmaf_available():
            err_console.print(
                "[red]error:[/] --vmaf requires libvmaf support in ffmpeg. "
                "Install ffmpeg with --enable-libvmaf."
            )
            sys.exit(1)

        # --- Validate --sample format ---
        if args.sample is not None:
            raw = args.sample.strip()
            if raw.endswith("%"):
                pct_str = raw[:-1]
                if not pct_str:
                    err_console.print(
                        f"[red]error:[/] invalid --sample value {args.sample!r}"
                    )
                    sys.exit(1)
                try:
                    pct = float(pct_str)
                except ValueError:
                    err_console.print(
                        f"[red]error:[/] invalid --sample value {args.sample!r}"
                    )
                    sys.exit(1)
                if not (0 < pct <= 100):
                    err_console.print(
                        "[red]error:[/] --sample percentage must be > 0 and ≤ 100"
                    )
                    sys.exit(1)
                args.sample = ("pct", pct / 100.0)
            else:
                try:
                    n = int(raw)
                except ValueError:
                    err_console.print(
                        f"[red]error:[/] invalid --sample value {args.sample!r}"
                    )
                    sys.exit(1)
                if n <= 0:
                    err_console.print(
                        "[red]error:[/] --sample must be a positive integer"
                    )
                    sys.exit(1)
                args.sample = ("count", n)

        _cmd_vmaf(args, console)
        return

    # --- Replace mode ---
    if args.replace:
        # --- Warn about ignored encoding flags ---
        ignored: list[str] = []
        if args.vmaf is not None:
            ignored.append("--vmaf")
        if args.crf is not None:
            ignored.append("--crf")
        if args.resize:
            ignored.append("--resize")
        if args.preset != "medium":
            ignored.append("--preset")
        if args.reencode_audio:
            ignored.append("--reencode-audio")
        if args.output_format:
            ignored.append("--format")
        if args.sample is not None:
            ignored.append("--sample")
        if args.vmaf_clips != _NUM_CLIPS:
            ignored.append("--vmaf-clips")
        if args.vmaf_clip_duration != _CLIP_DURATION:
            ignored.append("--vmaf-clip-duration")
        if ignored:
            err_console.print(
                f"[yellow]note:[/] --replace does no encoding; "
                f"ignoring: {', '.join(ignored)}"
            )
        _cmd_replace(args, console)
        return

    # --- Fallback CRF default ---
    if args.crf is None:
        args.crf = 23

    # --- Validate CRF (encode mode only) ---
    if not (0 <= args.crf <= 51):
        err_console.print("[red]error:[/] --crf must be between 0 and 51")
        sys.exit(1)

    if args.resize and not _valid_resize(args.resize):
        err_console.print(
            f"[red]error:[/] invalid --resize value '{args.resize}'"
            "  (valid: 720p, 1080p, 4k, or WxH e.g. 1280x720)"
        )
        sys.exit(1)

    # --- Encode mode ---
    # --- Warn about VMAF-only flags in encode mode ---
    if args.vmaf_clips != _NUM_CLIPS:
        err_console.print("[yellow]note:[/] --vmaf-clips has no effect without --vmaf")
    if args.vmaf_clip_duration != _CLIP_DURATION:
        err_console.print(
            "[yellow]note:[/] --vmaf-clip-duration has no effect without --vmaf"
        )

    _cmd_encode(args, console)


def _dedup_consecutive(raw: list[str]) -> list[str]:
    """Collapse consecutive identical lines into a single line with a repeat count.

    ["a", "a", "a", "b", "c", "c"] → ["a (3x)", "b", "c (2x)"]
    """
    if not raw:
        return []

    result: list[str] = []
    current = raw[0]
    count = 1

    for line in raw[1:]:
        if line == current:
            count += 1
        else:
            result.append(f"{current} ({count}x)" if count > 1 else current)
            current = line
            count = 1

    result.append(f"{current} ({count}x)" if count > 1 else current)
    return result


def _append_ffmpeg_log(path: Path, lines: list[str], tail: int = 100) -> None:
    """Append the last failed encode session + recent tail of the ffmpeg log.

    The ffmpeg log is a concatenation of per-encode sessions delimited by:

        ======== ... ========
        <ts>  rc=<code>  <label>
        cmd: <ffmpeg command>
        -------- ... --------
        <stderr output>

    Instead of a blind tail-chop (which can drown a crash in progress output),
    this extracts the *last failed session* first with a signal-name annotation,
    then appends the recent tail, and finishes with a compact rc-status summary
    of the last 20 sessions.
    """
    sep72 = "=" * 72

    _SIGNAL_NAMES: dict[int, str] = {
        -1: "SIGHUP (hangup)",
        -2: "SIGINT (interrupt)",
        -3: "SIGQUIT (quit)",
        -6: "SIGABRT (abort)",
        -9: "SIGKILL (killed)",
        -11: "SIGSEGV (segmentation fault — ffmpeg crashed)",
        -15: "SIGTERM (terminated)",
    }

    def _signal_name(rc: int) -> str:
        return _SIGNAL_NAMES.get(rc, f"signal {rc}")

    if not path.exists():
        lines.append("(not found)\n")
        return

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        lines.append("(empty)\n")
        return

    all_lines = content.splitlines()
    total = len(all_lines)

    # Walk sessions: find every === header followed by an rc= line
    session_starts: list[int] = []  # line index of each === header
    session_rc: dict[int, int] = {}  # start_idx → rc
    session_label: dict[int, str] = {}  # start_idx → label

    for i, line in enumerate(all_lines):
        if line == sep72 and i + 1 < total:
            rc_line = all_lines[i + 1]
            if "rc=" in rc_line:
                session_starts.append(i)
                m = re.search(r"rc=(-?\d+)", rc_line)
                if m:
                    rc = int(m.group(1))
                    session_rc[i] = rc
                    # extract label from "ts  rc=N  ...label..."
                    parts = rc_line.split(None, 2)
                    lbl = parts[2] if len(parts) > 2 else rc_line
                    session_label[i] = lbl

    # Identify the last failed session
    fail_idx: int | None = None
    for s in reversed(session_starts):
        if session_rc.get(s, 0) != 0:
            fail_idx = s
            break

    # Find its extent: next === or end of file
    def _session_end(start: int) -> int:
        for s in session_starts:
            if s > start:
                return s
        return total

    # ── Section 1: Last failed encode ──
    if fail_idx is not None:
        rc = session_rc.get(fail_idx, 0)
        label = session_label.get(fail_idx, "")
        fail_end = _session_end(fail_idx)
        session_lines = _dedup_consecutive(all_lines[fail_idx:fail_end])

        sig = _signal_name(rc)
        lines.append(f"\n[Last failed encode — rc={rc}  {sig}]\n")
        lines.append(f"  File: {label}\n")
        for sl in session_lines:
            lines.append(sl + "\n")
        lines.append(f"\n[End — {len(session_lines)} unique line groups]\n")

    # ── Section 2: Recent log tail ──
    tail_start = max(0, total - tail)
    # If the failed session sits inside the tail window, exclude its lines
    # to avoid duplication
    if fail_idx is not None and fail_idx >= tail_start:
        fail_end = _session_end(fail_idx)
        tail_pre: list[str] = []
        tail_post: list[str] = []
        for j in range(tail_start, total):
            if j < fail_idx:
                tail_pre.append(all_lines[j])
            elif j >= fail_end:
                tail_post.append(all_lines[j])
        if tail_pre:
            deduped = _dedup_consecutive(tail_pre)
            lines.append(
                f"\n[Recent log tail before failed session"
                f" ({len(tail_pre)} raw → {len(deduped)} groups)]\n"
            )
            for tl in deduped:
                lines.append(tl + "\n")
        if tail_post:
            deduped = _dedup_consecutive(tail_post)
            lines.append(
                f"\n[Recent log tail after failed session"
                f" ({len(tail_post)} raw → {len(deduped)} groups)]\n"
            )
            for tl in deduped:
                lines.append(tl + "\n")
    else:
        raw_tail = all_lines[tail_start:]
        deduped = _dedup_consecutive(raw_tail)
        lines.append(
            f"\n[Recent log tail ({len(raw_tail)} raw → {len(deduped)} unique groups"
            f" of {total} total lines)]\n"
        )
        for tl in deduped:
            lines.append(tl + "\n")

    # ── Section 3: Quick rc summary of recent sessions ──
    recent = session_starts[-20:]
    if recent:
        lines.append(f"\n[Recent session status ({len(recent)} sessions)]\n")
        # Show newest first
        for s in reversed(recent):
            rc = session_rc.get(s, 0)
            ts = all_lines[s + 1].split("rc=")[0].strip()
            sig = _signal_name(rc) if rc < 0 else ""
            status = "OK" if rc == 0 else "FAIL"
            lines.append(f"  {ts}  rc={rc}  {status:4s}  {sig}\n")


def _cmd_report(args: argparse.Namespace) -> None:
    """Write a diagnostic report to a timestamped file."""
    from datetime import datetime

    console = Console(highlight=False)

    # --report is standalone: reject any other non-default flags
    flag_names = [
        ("--yolo", args.yolo),
        ("--replace", args.replace),
        ("--dry-run", args.dry_run),
        ("--permanent", args.permanent),
        ("--cpu", args.cpu),
        ("--reencode-audio", args.reencode_audio),
        ("--no-upscale", args.no_upscale),
        ("--halt-on-increase", args.halt_on_increase),
        ("--resize", bool(args.resize)),
        ("--format", bool(args.output_format)),
        ("--crf", args.crf is not None),
        ("--preset", args.preset != "medium"),
    ]
    extra = [name for name, present in flag_names if present]
    if args.paths:
        extra.append("paths")

    if extra:
        err_console_ = Console(stderr=True, highlight=False)
        err_console_.print(
            f"[red]error:[/] --report cannot be combined with other options: "
            f"{', '.join(extra)}"
        )
        sys.exit(1)

    console.print("[bold]h265ify diagnostic report[/]\n")

    # Build report in memory
    lines: list[str] = []
    sep = "=" * 72

    def _append_file(path: Path, heading: str, max_lines: int = 100) -> None:
        lines.append(f"\n--- {heading} ---\n")
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                raw_lines = content.split("\n")
                file_lines = _dedup_consecutive(raw_lines)
                raw_count = len(raw_lines)
                deduped_count = len(file_lines)
                if deduped_count > max_lines:
                    lines.append(
                        f"[showing last {max_lines} of {raw_count} lines"
                        f" (deduped to {deduped_count})]\n"
                    )
                    file_lines = file_lines[-max_lines:]
                else:
                    if raw_count != deduped_count:
                        lines.append(
                            f"[{raw_count} lines, deduped to {deduped_count}]\n"
                        )
                for line in file_lines:
                    lines.append(line + "\n")
            else:
                lines.append("(empty)\n")
        else:
            lines.append("(not found)\n")

    # Header
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("h265ify diagnostic report\n")
    lines.append(f"Generated: {timestamp}\n")
    lines.append(f"Version:   {version('h265ify')}\n")
    lines.append(f"Python:    {sys.version}\n")
    lines.append(f"Platform:  {platform.platform()}\n")
    lines.append(f"Log dir:   {LOG_FILE.parent}\n")

    # Last exception
    if ERROR_LOG_FILE.exists():
        content = ERROR_LOG_FILE.read_text(encoding="utf-8").strip()
        if content:
            blocks = [b.strip() for b in content.split(sep) if b.strip()]
            recent = blocks[-3:] if len(blocks) > 1 else blocks
            lines.append("\n--- Last exception(s) ---\n")
            for b in recent:
                lines.append(f"{sep}\n")
                lines.append(b + "\n")
                lines.append(f"{sep}\n\n")
    else:
        lines.append("\n--- Last exception(s) ---\n(not found)\n")

    # Application log
    _append_file(LOG_FILE, "Application log (h265ify.log)", max_lines=100)

    # FFmpeg log — session-aware extraction
    lines.append("\n--- FFmpeg log (h265ify_ffmpeg.log) ---\n")
    _append_ffmpeg_log(FFMPEG_LOG_FILE, lines, tail=100)

    # Write to file
    report_path = (
        LOG_FILE.parent
        / f"h265ify_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    report_str = "".join(lines)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_str, encoding="utf-8")
    except OSError as e:
        console.print(f"[red]error:[/] could not write report: {e}")
        sys.exit(1)

    console.print(f"Report written to: [bold]{report_path}[/]")
    console.print(f"[dim]({len(report_str)} bytes)[/]")

    sys.exit(0)


def _cmd_replace(args: argparse.Namespace, console: Console) -> None:
    """Replace originals with their _h265 copies."""
    # --- Header ---
    if args.permanent and not args.dry_run:
        console.print(
            "[bold red]\u26a0  --permanent:[/] originals will be"
            " [bold red]permanently deleted[/] \u2014 not moved to trash"
        )
        console.print(f"[dim]log: {LOG_FILE}[/]  tag: {get_session_tag()}")
        console.print()
        answer = (
            console.input("[red]type 'yes' to confirm permanent deletion:[/] ")
            .strip()
            .lower()
        )
        if answer not in ("y", "yes"):
            console.print("aborted.")
            sys.exit(0)
        console.print()
    else:
        console.print(
            "[bold]replace[/]  [dim]originals \u2192 trash"
            + ("  (dry-run)" if args.dry_run else "")
            + f"  log: {LOG_FILE}[/]  tag: {get_session_tag()}"
        )
        console.print()

    pairs = find_replace_pairs(args.paths, console)

    if not pairs:
        console.print("no _h265 files with matching originals found.")
        sys.exit(0)

    console.print(f"[bold]found {len(pairs)} replacement pairs[/]\n")

    # Compute sizes before renaming (h265 files won't exist after run_replace)
    total_original = sum(p.original_path.stat().st_size for p in pairs)
    total_h265 = sum(p.h265_path.stat().st_size for p in pairs)

    all_replace_paths = [p.original_path for p in pairs] + [p.h265_path for p in pairs]
    replace_display = compute_display_names(all_replace_paths)

    replaced, skipped = run_replace(
        pairs,
        dry_run=args.dry_run,
        permanent=args.permanent,
        console=console,
        display_names=replace_display,
    )

    console.print()
    if args.dry_run:
        console.print(f"  {replaced} would be replaced")
    else:
        console.print(f"  [bold]{replaced} replaced[/], {skipped} skipped")

    # Show space savings
    if total_original > 0 and total_h265 > 0:
        saved = total_original - total_h265
        pct = (1 - total_h265 / total_original) * 100
        saved_str = format_size(saved) if saved > 0 else "0 B"
        sign = "" if saved >= 0 else "+"
        console.print(
            f"  space saved: {saved_str} ({sign}{pct:.1f}%)"
            f"  {format_size(total_original)} → {format_size(total_h265)}"
        )


def _cmd_vmaf(args: argparse.Namespace, console: Console) -> None:
    """Run VMAF evaluation and recommend optimal CRF for each file (no encoding)."""
    # --- Detect or force encoder ---
    if args.cpu:
        encoder = Encoder(name="libx265", is_hardware=False, label="CPU (libx265)")
    else:
        encoder = detect_encoder()
    hw_note = ""
    if args.cpu:
        hw_note = " [dim](--cpu forced)[/]"
    elif not encoder.is_hardware:
        hw_note = " [yellow](no hardware encoder detected)[/]"

    sample_note = ""
    if args.sample is not None:
        stype, sval = args.sample
        if stype == "pct":
            sample_note = f"  sample={sval * 100:.0f}%"
        else:
            sample_note = f"  sample={sval}"

    logger.info(
        f"vmaf-eval: encoder={encoder.name}  target_vmaf={args.vmaf}"
        f"  preset={args.preset}"
        f"  clips={args.vmaf_clips}x{args.vmaf_clip_duration}s{sample_note}"
    )
    parts = [f"[bold green]{encoder.label}[/]{hw_note}"]
    parts.append(f"VMAF target [green]{args.vmaf}[/]")
    parts.append(f"preset [green]{args.preset}[/]")
    parts.append(f"clips [cyan]{args.vmaf_clips}x{args.vmaf_clip_duration}s[/]")
    if args.sample is not None:
        if stype == "pct":
            parts.append(f"sample [cyan]{sval * 100:.0f}%[/]")
        else:
            parts.append(f"sample [cyan]{sval}[/]")
    console.print(" ".join(parts))
    console.print(f"[dim]log: {LOG_FILE}[/]  tag: {get_session_tag()}")
    console.print()

    # --- Find files ---
    video_files = find_video_files(args.paths, console)
    if not video_files:
        console.print("no video files found.")
        sys.exit(0)

    console.print(f"found {len(video_files)} video files")

    # --- Probe ---
    probe_results = probe_files(video_files, console=console)
    if not probe_results:
        console.print("[yellow]no valid video files found after probing.[/]")
        sys.exit(0)

    # --- Filter out already-h265 files ---
    to_evaluate = [pr for pr in probe_results if not pr.is_h265]
    skipped_h265 = [pr for pr in probe_results if pr.is_h265]

    # Compute display names (trim common prefix) for narrow terminals
    vmaf_all_paths = [pr.path for pr in probe_results]
    vmaf_display = compute_display_names(vmaf_all_paths) if vmaf_all_paths else {}

    if skipped_h265:
        console.print()
        for pr in skipped_h265:
            console.print(
                f"  skip  {vmaf_display.get(pr.path, pr.path.name)}  (already h265)"
            )

    if not to_evaluate:
        if skipped_h265:
            console.print()
            console.print("nothing to evaluate (all files are already h265).")
        else:
            console.print("nothing to evaluate.")
        return

    # --- Random sampling (--sample) ---
    if args.sample is not None:
        sample_type, sample_value = args.sample
        total_eligible = len(to_evaluate)
        if sample_type == "pct":
            sample_count = max(1, int(total_eligible * sample_value))
        else:
            sample_count = sample_value
        sample_count = min(sample_count, total_eligible)

        if sample_count < total_eligible:
            to_evaluate = random.sample(to_evaluate, sample_count)
            console.print(
                f"  random sample: {sample_count} of {total_eligible} eligible files"
            )

    if args.dry_run:
        console.print("would evaluate:")
        for pr in to_evaluate:
            console.print(f"  {vmaf_display.get(pr.path, pr.path.name)}")
        return

    # --- Run VMAF evaluation (sequential) ---
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
    )

    n_probe = len(to_evaluate)
    results: dict[Path, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            f"VMAF evaluation ({n_probe} file(s), target: {args.vmaf})…",
            total=None,
        )

        def _probe_done(msg: str) -> None:
            """Callback from determine_crf for live progress updates."""
            progress.update(task, description=msg)

        interrupted = False
        try:
            for idx, pr in enumerate(to_evaluate, 1):
                progress.update(
                    task,
                    description=f"[{idx}/{n_probe}] {vmaf_display.get(pr.path, pr.path.name)}…",
                )

                lines: list[str] = []
                crf = determine_crf(
                    pr.path,
                    pr,
                    encoder,
                    target_vmaf=args.vmaf,
                    preset=args.preset,
                    output_lines=lines,
                    progress_callback=_probe_done,
                    num_clips=args.vmaf_clips,
                    clip_duration=args.vmaf_clip_duration,
                )
                results[pr.path] = crf
                logger.info(f"vmaf-eval: {pr.path.name} -> CRF {crf}")

                progress.update(
                    task,
                    description=f"VMAF evaluation ({n_probe} file(s), target: {args.vmaf})…",
                )
                console.print(
                    f"  [{idx}/{n_probe}] {vmaf_display.get(pr.path, pr.path.name)}"
                )
                for line in lines:
                    console.print(line)
        except KeyboardInterrupt:
            interrupted = True
            console.print()
            if results:
                console.print("  [yellow]interrupted — partial results below[/]")
            else:
                console.print("  [yellow]interrupted[/]")

    # --- Summary ---
    if interrupted or not results:
        if results:
            console.print()
            for pr in to_evaluate:
                if pr.path in results:
                    crf_val = results[pr.path]
                    console.print(
                        f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
                        f" \u2192 CRF [bold]{crf_val}[/]"
                    )
            console.print()
            console.print("  [yellow]VMAF evaluation incomplete (partial results)[/]")
        else:
            console.print()
            console.print("  [yellow]VMAF evaluation stopped[/]")
        sys.exit(130)

    # Full results summary
    crf_values = list(results.values())
    min_crf, max_crf = min(crf_values), max(crf_values)
    console.print()
    console.print("[bold]VMAF evaluation complete.[/]")
    if min_crf == max_crf:
        console.print(f"  all files: CRF [bold]{min_crf}[/]")
    else:
        console.print(f"  CRF range: {min_crf} \u2013 {max_crf}")
    console.print()
    for pr in to_evaluate:
        crf = results[pr.path]
        console.print(
            f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
            f" \u2192 CRF [bold]{crf}[/]"
        )
    console.print()
    console.print("  [dim]Use --crf <N> to encode with your chosen value.[/]")


def _cmd_encode(args: argparse.Namespace, console: Console) -> None:
    """Run the encoding pipeline."""
    # --- Detect or force encoder ---
    if args.cpu:
        encoder = Encoder(name="libx265", is_hardware=False, label="CPU (libx265)")
    else:
        encoder = detect_encoder()
    hw_note = ""
    if args.cpu:
        hw_note = " [dim](--cpu forced)[/]"
    elif not encoder.is_hardware:
        hw_note = " [yellow](no hardware encoder detected)[/]"

    crf_display = str(args.crf)
    logger_crf = str(args.crf)
    logger.info(
        f"encoder={encoder.name}  crf={logger_crf}  preset={args.preset}"
        + (f"  resize={args.resize}" if args.resize else "")
        + ("  yolo" if args.yolo else "")
        + ("  reencode-audio" if args.reencode_audio else "")
    )
    parts = [f"[bold green]{encoder.label}[/]{hw_note}"]
    parts.append(f"CRF [green]{crf_display}[/]")
    parts.append(f"preset [green]{args.preset}[/]")
    if args.resize:
        parts.append(f"resize [cyan]{args.resize}[/]")
    console.print(" ".join(parts))
    console.print(f"[dim]log: {LOG_FILE}[/]  tag: {get_session_tag()}")
    if args.yolo and not args.dry_run:
        if args.permanent:
            console.print(
                "[bold red]\u26a0  --permanent:[/] originals will be"
                " [bold red]permanently deleted[/] after encoding \u2014 not moved to trash"
            )
        else:
            console.print(
                "[yellow]\u26a0  --yolo:[/] originals will be moved to trash after encoding"
            )
    console.print()

    # --- Yolo confirmation ---
    if args.yolo and args.permanent and not args.dry_run:
        answer = (
            console.input("[red]type 'yes' to confirm permanent deletion:[/] ")
            .strip()
            .lower()
        )
        if answer not in ("y", "yes"):
            console.print("aborted.")
            sys.exit(0)
        console.print()

    # --- Find files ---
    video_files = find_video_files(args.paths, console)
    if not video_files:
        console.print("no video files found.")
        sys.exit(0)

    console.print(f"found {len(video_files)} video files")

    # --- Probe ---
    probe_results = probe_files(video_files, console=console)
    if not probe_results:
        console.print("[yellow]no valid video files found after probing.[/]")
        sys.exit(0)

    # --- Prepare jobs ---
    jobs, skipped = prepare_jobs(
        probe_results,
        replace=args.yolo,
        output_format=args.output_format,
    )

    if not jobs:
        if skipped:
            console.print()
            for r in skipped:
                if r.is_h265:
                    console.print(f"  skip  {r.path.name}  (already h265)")
                else:
                    out = get_output_path(r.path, args.yolo, args.output_format)
                    console.print(f"  skip  {r.path.name}  ({out.name} exists)")
            console.print()
            all_h265 = all(r.is_h265 for r in skipped)
            if all_h265:
                console.print("nothing to do (all files are already h265).")
            else:
                console.print("nothing to do (all output files already exist).")
        else:
            console.print("nothing to do.")
        sys.exit(0)

    # Compute display names (trim common prefix) for narrow terminals
    all_input_paths = [j.input_path for j in jobs] + [r.path for r in skipped]
    display_names = compute_display_names(all_input_paths)
    for j in jobs:
        j.display_name = display_names.get(j.input_path)

    n_jobs = len(jobs)
    total_input = sum(j.probe_result.file_size for j in jobs)
    size_str = f"  ({format_size(total_input)})" if total_input > 0 else ""
    console.print(f"[bold]{n_jobs} file(s) to encode[/]{size_str}")
    if skipped:
        h265_count = sum(1 for r in skipped if r.is_h265)
        exists_count = len(skipped) - h265_count
        reasons: list[str] = []
        if h265_count:
            reasons.append(f"{h265_count} already h265")
        if exists_count:
            reasons.append(f"{exists_count} output exists")
        console.print(f"  skip {len(skipped)} file(s) ({', '.join(reasons)})")
    console.print()

    def _on_job_complete(job: EncodeJob, result: EncodeResult) -> None:
        elapsed = (
            f"{result.elapsed:.0f}s"
            if result.elapsed < 60
            else f"{result.elapsed / 60:.1f}m"
        )
        name = job.display_name or job.input_path.name

        # Fit name to available terminal width.
        # Fixed overhead:
        #   success: "  ✓ " (4) + in_size (9) + " → " (3) + out_size (9)
        #          + "  (" (2) + pct (4) + ")  in " (6) + elapsed (5) = 42
        #   failure: "  ✗ " (4) + "  failed  in " (13) + elapsed (5) = 22
        name_width = max(20, min(50, console.width - 42))
        if len(name) > name_width:
            name = name[: name_width - 3] + "..."

        if not result.success:
            console.print(
                f"  [red]\u2717[/] {name:<{name_width}}  [red]failed[/]  in {elapsed:>5}"
            )
        elif result.skipped:
            pct = (result.output_size / result.input_size - 1) * 100
            console.print(
                f"  [yellow]\u21b7[/] {name:<{name_width}} [yellow]skipped[/] (output larger: +{pct:.0f}%)  in {elapsed:>5}"
            )
        elif result.output_size > 0:
            pct = (1 - result.output_size / result.input_size) * 100
            if pct > 0:
                pct_str = f"[green]-{pct:.0f}%[/]"
            elif pct < -0.5:
                pct_str = f"[red]+{-pct:.0f}%[/]"
            else:
                pct_str = "~0%"
            console.print(
                f"  [green]\u2713[/] {name:<{name_width}}"
                f" {format_size(result.input_size):>9} → {format_size(result.output_size):>9}"
                f"  ({pct_str})"
                f"  in {elapsed:>5}"
            )
        else:
            console.print(
                f"  [green]\u2713[/] {name:<{name_width}} {'-':>9} → {'-':>9}  in {elapsed:>5}"
            )

    results, interrupted = run_pipeline(
        jobs=jobs,
        encoder=encoder,
        crf=args.crf,
        replace=args.yolo,
        dry_run=args.dry_run,
        output_format=args.output_format,
        permanent=args.permanent,
        preset=args.preset,
        resize=args.resize,
        no_upscale=args.no_upscale,
        reencode_audio=args.reencode_audio,
        halt_on_increase=args.halt_on_increase,
        console=console,
        on_job_complete=_on_job_complete,
    )

    print_summary(results, skipped, args.dry_run, console=console)

    # Exit with appropriate code
    if interrupted:
        sys.exit(130)
    elif any(not r.success for r in results):
        sys.exit(1)
