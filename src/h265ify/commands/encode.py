"""``--encode`` (default) subcommand — the encoding pipeline."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from ..encoder import format_size
from ..hardware import resolve_encoder
from ..logger import LOG_FILE, get_session_tag, logger
from ..pipeline import (
    EncodeJob,
    EncodeResult,
    check_disk_space,
    compute_display_names,
    find_video_files,
    get_output_path,
    prepare_jobs,
    print_summary,
    probe_files,
    run_pipeline,
)


def _print_job_result(
    job: EncodeJob, result: EncodeResult, console: Console
) -> None:
    """Print a single-file encode result line with Rich formatting."""
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
            f"  [yellow]\u21b7[/] {name:<{name_width}}"
            f" [yellow]skipped[/] (output larger: +{pct:.0f}%)  in {elapsed:>5}"
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
            f" {format_size(result.input_size):>9} →"
            f" {format_size(result.output_size):>9}"
            f"  ({pct_str})"
            f"  in {elapsed:>5}"
        )
    else:
        console.print(
            f"  [green]\u2713[/] {name:<{name_width}}"
            f" {'-':>9} → {'-':>9}  in {elapsed:>5}"
        )


def cmd_encode(args: argparse.Namespace, console: Console) -> None:
    """Run the encoding pipeline."""
    encoder, hw_note = resolve_encoder(cpu=args.cpu)

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
                " [bold red]permanently deleted[/] after encoding"
                " \u2014 not moved to trash"
            )
        else:
            console.print(
                "[yellow]\u26a0  --yolo:[/] originals will be moved to trash"
                " after encoding"
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
                    out = get_output_path(
                        r.path, args.yolo, args.output_format
                    )
                    console.print(f"  skip  {r.path.name}  ({out.name} exists)")
            console.print()
            all_h265 = all(r.is_h265 for r in skipped)
            if all_h265:
                console.print("nothing to do (all files are already h265).")
            else:
                console.print(
                    "nothing to do (all output files already exist)."
                )
        else:
            console.print("nothing to do.")
        sys.exit(0)

    # --- Disk space check ---
    if not args.dry_run and not args.ignore_full_disk:
        if not check_disk_space(
            jobs,
            yolo=args.yolo,
            output_format=args.output_format,
            console=console,
        ):
            sys.exit(1)

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
        console.print(
            f"  skip {len(skipped)} file(s) ({', '.join(reasons)})"
        )
    console.print()

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
        on_job_complete=lambda job, result: _print_job_result(
            job, result, console
        ),
    )

    print_summary(results, skipped, args.dry_run, console=console)

    if interrupted:
        sys.exit(130)
    elif any(not r.success for r in results):
        sys.exit(1)