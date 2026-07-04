"""h265ify - a zero-fuss h265/HEVC video encoder wrapper for ffmpeg."""

from __future__ import annotations

import argparse
import platform
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
    install_excepthook,
)
from .pipeline import (
    EncodeJob,
    EncodeResult,
    find_replace_pairs,
    find_video_files,
    get_output_path,
    prepare_jobs,
    print_summary,
    probe_files,
    run_pipeline,
    run_replace,
)


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
        default=23,
        metavar="N",
        help="quality: 0–51, lower = better (default: 23). "
        "For hardware encoders, mapped to the closest equivalent.",
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

    # --- Log session start ---
    _ver = version("h265ify")
    logger.info(f"=== h265ify {_ver} ===")
    mode = "dry-run" if args.dry_run else ("replace" if args.replace else "encode")
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

    # --- Replace mode ---
    if args.replace:
        # --- Warn about ignored encoding flags ---
        ignored: list[str] = []
        if args.crf != 23:
            ignored.append("--crf")
        if args.resize:
            ignored.append("--resize")
        if args.preset != "medium":
            ignored.append("--preset")
        if args.reencode_audio:
            ignored.append("--reencode-audio")
        if args.output_format:
            ignored.append("--format")
        if ignored:
            err_console.print(
                f"[yellow]note:[/] --replace does no encoding; "
                f"ignoring: {', '.join(ignored)}"
            )
        _cmd_replace(args, console)
        return

    # --- Validate CRF (encode mode only) ---
    if not (0 <= args.crf <= 51):
        err_console.print("[red]error:[/] --crf must be between 0 and 51")
        sys.exit(1)

    # --- Validate --resize (encode mode only) ---
    if args.resize and not _valid_resize(args.resize):
        err_console.print(
            f"[red]error:[/] invalid --resize value '{args.resize}'"
            "  (valid: 720p, 1080p, 4k, or WxH e.g. 1280x720)"
        )
        sys.exit(1)

    # --- Encode mode ---
    _cmd_encode(args, console)


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
        ("--crf", args.crf != 23),
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
                file_lines = content.split("\n")
                if len(file_lines) > max_lines:
                    lines.append(
                        f"[showing last {max_lines} of {len(file_lines)} lines]\n"
                    )
                    file_lines = file_lines[-max_lines:]
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

    # FFmpeg log
    _append_file(FFMPEG_LOG_FILE, "FFmpeg log (h265ify_ffmpeg.log)", max_lines=100)

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
        console.print(f"[dim]log: {LOG_FILE}[/]")
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
            + f"  log: {LOG_FILE}[/]"
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

    replaced, skipped = run_replace(
        pairs, dry_run=args.dry_run, permanent=args.permanent, console=console
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

    logger.info(
        f"encoder={encoder.name}  crf={args.crf}  preset={args.preset}"
        + (f"  resize={args.resize}" if args.resize else "")
        + ("  yolo" if args.yolo else "")
        + ("  reencode-audio" if args.reencode_audio else "")
    )
    parts = [f"[bold green]{encoder.label}[/]{hw_note}"]
    parts.append(f"CRF [green]{args.crf}[/]")
    parts.append(f"preset [green]{args.preset}[/]")
    if args.resize:
        parts.append(f"resize [cyan]{args.resize}[/]")
    console.print(" ".join(parts))
    console.print(f"[dim]log: {LOG_FILE}[/]")
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
        name = job.input_path.name

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
