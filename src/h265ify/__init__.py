"""h265ify - a zero-fuss h265/HEVC video encoder wrapper for ffmpeg."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from rich.console import Console

from .encoder import format_size
from .hardware import Encoder, detect_encoder
from .logger import LOG_FILE, logger
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
  h265ify --tune animation anime.mkv  optimize for animation
  h265ify --cpu video.mkv          force software encoding (libx265)
""",
    )

    parser.add_argument(
        "paths",
        nargs="+",
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
        action="store_true",
        help="show what would happen without encoding or replacing anything",
    )
    output_grp.add_argument(
        "--permanent",
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
        "--tune",
        choices=["animation", "grain", "stillimage", "fastdecode", "zerolatency"],
        help="tuning profile for specific content types (libx265 only)",
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
    parser.add_argument(
        "--version",
        action="version",
        version=f"h265ify {version('h265ify')}",
        help="print version and exit",
    )

    args = parser.parse_args()

    console = Console(highlight=False)
    err_console = Console(stderr=True, highlight=False)

    # --- Log session start ---
    _ver = version("h265ify")
    logger.info(f"=== h265ify {_ver} ===")
    mode = "dry-run" if args.dry_run else ("replace" if args.replace else "encode")
    logger.info(f"mode={mode}  paths={', '.join(str(p) for p in args.paths)}")
    logger.info(f"log: {LOG_FILE}")

    # --- Mutual exclusivity ---
    if args.replace and args.yolo:
        err_console.print("[red]error:[/] --replace and --yolo are mutually exclusive.")
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
        if args.tune:
            ignored.append("--tune")
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

    replaced, skipped = run_replace(
        pairs, dry_run=args.dry_run, permanent=args.permanent, console=console
    )

    console.print()
    if args.dry_run:
        console.print(f"  {replaced} would be replaced")
    else:
        console.print(f"  [bold]{replaced} replaced[/], {skipped} skipped")


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
        + (f"  tune={args.tune}" if args.tune else "")
        + (f"  resize={args.resize}" if args.resize else "")
        + ("  yolo" if args.yolo else "")
        + ("  reencode-audio" if args.reencode_audio else "")
    )
    parts = [f"[bold green]{encoder.label}[/]{hw_note}"]
    parts.append(f"CRF [green]{args.crf}[/]")
    parts.append(f"preset [green]{args.preset}[/]")
    if args.tune:
        parts.append(f"tune [cyan]{args.tune}[/]")
        if encoder.is_hardware:
            console.print(
                f"  [yellow]note:[/] --tune is ignored by {encoder.label} (libx265 only)"
            )
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

    console.print(f"[bold]encoding {len(jobs)} file(s)[/]")
    if skipped:
        console.print()
        for r in skipped:
            if r.is_h265:
                console.print(f"  skip  {r.path.name}  (already h265)")
            else:
                out = get_output_path(r.path, args.yolo, args.output_format)
                console.print(f"  skip  {r.path.name}  ({out.name} exists)")
    console.print()

    def _on_job_complete(job: EncodeJob, result: EncodeResult) -> None:
        elapsed = (
            f"{result.elapsed:.0f}s"
            if result.elapsed < 60
            else f"{result.elapsed / 60:.1f}m"
        )
        name = job.input_path.name

        # Fit name to available terminal width.
        # Fixed overhead: "  ✓ " (4) + size (10) + "  " (2) + reduction (8) + "  " (2) + elapsed (5) = 31
        name_width = max(20, min(50, console.width - 31))
        if len(name) > name_width:
            name = name[: name_width - 3] + "..."

        if not result.success:
            console.print(
                f"  [red]\u2717[/] {name:<{name_width}} {'-':>10}  [red]failed[/]  {elapsed:>4}"
            )
        elif result.output_size > 0:
            pct = (1 - result.output_size / result.input_size) * 100
            if pct > 0:
                reduction = f"[green]-{pct:.0f}%[/]"
            elif pct < -0.5:
                reduction = f"[red]+{-pct:.0f}%[/]"
            else:
                reduction = "~0%"
            console.print(
                f"  [green]\u2713[/] {name:<{name_width}} {format_size(result.output_size):>10}  {reduction:>8}  {elapsed:>4}"
            )
        else:
            console.print(
                f"  [green]\u2713[/] {name:<{name_width}} {'-':>10}  {'-':>8}  {elapsed:>4}"
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
        tune=args.tune,
        resize=args.resize,
        no_upscale=args.no_upscale,
        reencode_audio=args.reencode_audio,
        console=console,
        on_job_complete=_on_job_complete,
    )

    print_summary(results, skipped, args.dry_run, console=console)

    # Exit with appropriate code
    if interrupted:
        sys.exit(130)
    elif any(not r.success for r in results):
        sys.exit(1)
