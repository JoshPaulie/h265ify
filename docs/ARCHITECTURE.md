# Architecture

## High-level pipeline

```
 ┌──────────────────┐
 │  CLI (__init__)  │
 │  parse args,     │
 │  detect encoder  │
 └────────┬─────────┘
          │
 ┌────────▼──────────┐
 │  find_video_files │
 │  walk paths,      │
 │  filter by ext,   │
 │  exclude *_h265   │
 └────────┬──────────┘
          │
 ┌────────▼─────────┐
 │   probe_files    │
 │  ffprobe each,   │
 │  collect metadata│
 └────────┬─────────┘
          │
 ┌────────▼─────────┐
 │   prepare_jobs   │
 │  skip already    │
 │  h265, skip      │
 │  existing output │
 └────────┬─────────┘
          │
 ┌────────▼─────────┐
 │   run_pipeline   │
 │  sequential      │
 └────────┬─────────┘
          │
 ┌────────▼─────────┐
 │  print_summary   │
 │  sizes, time,    │
 │  skipped/failed  │
 └──────────────────┘
```

## Encode path (per file)

Every encode (normal or `--yolo`) uses an atomic temp file. The final output only appears if the encode completes successfully.

```
  ┌───────────┐
  │ input.mp4 │
  └─────┬─────┘
        │
  ┌─────▼───────────────────────────────────────────┐
  │          build_command(input, tmp_output)       │
  │  - encoder + quality flags                      │
  │  - resize filter                                │
  │  - HDR metadata passthrough                     │
  │  - audio: stream-copy or re-encode              │
  │  - subtitles: mov_text (MP4) or copy (MKV)      │
  │  - container flags: hvc1, faststart (MP4 only)  │
  └─────┬───────────────────────────────────────────┘
        │
  ┌─────▼──────────────────────┐
  │   ffmpeg -y ... tmp.mp4    │
  │   subprocess.Popen(stderr) │
  │   real-time progress (%)   │
  └─────┬──────────────────────┘
        │
     success?
     ╱       ╲
   yes        no
   ╱           ╲
  ▼             ▼
┌───────────┐  ┌───────────────────┐
│ os.replace│  │ unlink tmp.mp4    │
│ tmp → out │  │ (clean up trash)  │
└──────┬────┘  └────────┬──────────┘
       │                │
       ▼                ▼
  ┌────────┐       ┌─────────┐
  │  done  │       │  failed │
  └────────┘       │  halt   │
                   └─────────┘
```

### Temp file naming

```
Normal mode:   video.mp4  →  video_h265.h265-tmp.mp4  →  video_h265.mp4
--yolo mode:   video.mp4  →  video.h265-tmp.mp4       →  video.mp4
```

The temp suffix `.h265-tmp` is inserted before the container extension. On success, `os.replace()` atomically renames the temp file to the final output path.

## Crash recovery

No state file needed. The filesystem is the source of truth:

```
  Final output exists?
    ├── yes → encode completed.  Skip.
    └── no  → check for temp file.
                ├── exists → previous attempt crashed.
                │            ffmpeg -y overwrites it, re-encode.
                └── absent → fresh encode needed.
```

Because the temp file only becomes the final output via `os.replace()` (atomic on all modern filesystems), a partially-written file can never appear at the final path. Power loss, kill -9, kernel panic: no corruption.

## `--replace` mode (separate path)

No encoding happens. Finds existing `*_h265.*` files and swaps them with their originals.

```
  ┌──────────────────────┐
  │  find_replace_pairs  │
  │  scan paths for      │
  │  *_h265.* files      │
  └──────────┬───────────┘
             │
     for each pair:
     ┌───────▼────────────────────────────────┐
     │  original found?                       │
     │   ├── yes → delete original (trash or  │
     │   │         permanent), rename _h265   │
     │   │         to original stem + new ext │
     │   └── no  → warn, skip                 │
     └────────────────────────────────────────┘

  Example:
    video_h265.mp4 + video.mkv
      → trash video.mkv
      → rename video_h265.mp4 → video.mp4
```

## Module map

```
src/h265ify/
  __init__.py    CLI entry point, argparse, orchestration
  encoder.py     ffmpeg command builder, subprocess runner
  hardware.py    encoder detection, preset/quality mappings
  pipeline.py    file discovery, job prep, encode loop, replace mode
  probe.py       ffprobe wrapper, metadata extraction
```

Each module uses `from __future__ import annotations`, dataclasses for structured data, and `pathlib.Path` exclusively.
