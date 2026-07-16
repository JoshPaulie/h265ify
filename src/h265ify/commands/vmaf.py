"""``--vmaf`` subcommand — VMAF-based CRF evaluation (no encoding)."""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..hardware import resolve_encoder
from ..logger import LOG_FILE, get_session_tag, logger
from ..pipeline import compute_display_names, find_video_files, probe_files
from ..vmaf import determine_crf, estimate_crf_size_ratio, vmaf_available

from .shared import validate_sample_arg


def cmd_vmaf(args: argparse.Namespace, console: Console) -> None:
    """Run VMAF evaluation and recommend optimal CRF for each file (no encoding)."""
    # --- Validate --vmaf value ---
    if not (0 <= args.vmaf <= 100):
        console.print("[red]error:[/] --vmaf must be between 0 and 100")
        sys.exit(1)

    # --- Check ffmpeg has libvmaf ---
    if not vmaf_available():
        console.print(
            "[red]error:[/] --vmaf requires libvmaf support in ffmpeg."
            " Install ffmpeg with --enable-libvmaf."
        )
        sys.exit(1)

    # --- Validate --sample ---
    validate_sample_arg(args, console)

    # --- Reject encoding flags (mutually exclusive with --vmaf) ---
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
        console.print(
            f"[red]error:[/] --vmaf is mutually exclusive with"
            f" encoding flags: {', '.join(encoding_conflicts)}"
        )
        sys.exit(1)

    encoder, hw_note = resolve_encoder(cpu=args.cpu)

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
    n_probe = len(to_evaluate)
    results: dict[Path, float] = {}
    probe_data: dict[Path, list[tuple[int, int]]] = {}

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
                    description=(
                        f"[{idx}/{n_probe}] "
                        f"{vmaf_display.get(pr.path, pr.path.name)}…"
                    ),
                )

                lines: list[str] = []

                # Collector for probe encoded sizes for this file
                _this_probe_data: list[tuple[int, int]] = []

                def _on_crf_probe(
                    crf: int, min_vmaf: float, encoded_bytes: int
                ) -> None:
                    _this_probe_data.append((crf, encoded_bytes))

                crf = determine_crf(
                    pr.path,
                    pr,
                    encoder,
                    target_vmaf=args.vmaf,
                    preset=args.preset,
                    output_lines=lines,
                    progress_callback=_probe_done,
                    on_crf_probe=_on_crf_probe,
                    num_clips=args.vmaf_clips,
                    clip_duration=args.vmaf_clip_duration,
                )
                results[pr.path] = crf
                if _this_probe_data:
                    probe_data[pr.path] = _this_probe_data
                logger.info(f"vmaf-eval: {pr.path.name} -> CRF {crf:.1f}")

                progress.update(
                    task,
                    description=(
                        f"VMAF evaluation ({n_probe} file(s),"
                        f" target: {args.vmaf})…"
                    ),
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

    if interrupted or not results:
        if results:
            console.print()
            for pr in to_evaluate:
                if pr.path in results:
                    crf_val = results[pr.path]
                    crf_int = int(math.floor(crf_val))
                    if crf_val == float(crf_int):
                        display = (
                            f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
                            f" \u2192 CRF [bold]{crf_int}[/]"
                        )
                    else:
                        display = (
                            f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
                            f" \u2192 CRF [bold]{crf_int}[/]"
                            f"  [dim](from {crf_val:.1f})[/]"
                        )
                    console.print(display)
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

    # Safe batch CRF (most conservative across all files)
    if len(crf_values) >= 2:
        safe_crf = math.floor(min(crf_values))
        safe_val = int(safe_crf)
        if min_crf == max_crf:
            console.print(f"  all files: CRF [bold]{min_crf:.1f}[/]")
        else:
            console.print(f"  CRF range: {min_crf:.1f} \u2013 {max_crf:.1f}")
        console.print(
            f"  safe batch CRF: [bold]{safe_val}[/]"
            f" (most conservative across {len(crf_values)} files)"
        )

        # Projected savings between safe CRF and safe_crf + 1
        all_ratios: list[float] = []
        for pr in to_evaluate:
            if pr.path in probe_data:
                ratio = estimate_crf_size_ratio(
                    probe_data[pr.path],
                    from_crf=float(safe_val),
                    to_crf=float(safe_val + 1),
                )
                if ratio < 1.0:
                    all_ratios.append(ratio)
        if all_ratios:
            avg_ratio = sum(all_ratios) / len(all_ratios)
            savings_pct = (1 - avg_ratio) * 100
            min_savings = (1 - max(all_ratios)) * 100
            max_savings = (1 - min(all_ratios)) * 100
            if min_savings == max_savings:
                console.print(
                    f"  vs CRF {safe_val + 1}: projected"
                    f" [green]~{savings_pct:.0f}%[/] smaller"
                )
            else:
                console.print(
                    f"  vs CRF {safe_val + 1}: projected"
                    f" [green]~{savings_pct:.0f}%[/] smaller"
                    f" (range: {min_savings:.0f}\u2013{max_savings:.0f}% across files)"
                )
        else:
            console.print(
                f"  vs CRF {safe_val + 1}: projected ~12% smaller (rule of thumb)"
            )
    else:
        # Single file
        single_crf = crf_values[0]
        safe_int = int(math.floor(single_crf))
        if single_crf == float(safe_int):
            console.print(f"  CRF [bold]{safe_int}[/]")
        else:
            console.print(
                f"  CRF [bold]{safe_int}[/]  [dim](from {single_crf:.1f})[/]"
            )

    console.print()
    for pr in to_evaluate:
        crf = results[pr.path]
        crf_int = int(math.floor(crf))
        safe_note = ""
        if len(crf_values) >= 2:
            if crf_int < safe_val:
                safe_note = (
                    f" [dim](more conservative than batch CRF {safe_val})[/]"
                )
            elif crf_int > safe_val:
                safe_note = (
                    f" [dim](less conservative than batch CRF {safe_val})[/]"
                )
        if crf == float(crf_int):
            display = f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
            display += f" \u2192 CRF [bold]{crf_int}[/]{safe_note}"
        else:
            display = f"  [green]{vmaf_display.get(pr.path, pr.path.name)}[/]"
            display += (
                f" \u2192 CRF [bold]{crf_int}[/]"
                f"  [dim](from {crf:.1f})[/]{safe_note}"
            )
        console.print(display)
    console.print()
    console.print("  [dim]Use --crf <N> to encode with your chosen value.[/]")