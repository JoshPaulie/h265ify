# Architecture

## High-level pipeline

```mermaid
flowchart LR
    CLI["CLI (__init__)
parse args, detect encoder"]
    Find["find_video_files
walk paths, filter by ext,
exclude *_h265"]
    Probe["probe_files
ffprobe each, collect metadata"]
    Jobs["prepare_jobs
skip already h265,
skip existing output"]
    Run["run_pipeline
sequential encode loop
auto-skip larger output
--halt-on-increase stops
batch on first oversize"]
    Summary["print_summary
sizes, time,
skipped (h265/exists/larger)/failed"]

    CLI --> Find --> Probe --> Jobs --> Run --> Summary
```

## Encode path (per file)

Every encode (normal or `--yolo`) uses an atomic temp file. The final output only appears if the encode completes successfully. Size comparisons happen at two points:

- **Mid-stream**: a `cancel_check` callback polls the temp file every ~second; if it exceeds the original, ffmpeg is killed immediately.
- **Post-encode**: after ffmpeg exits cleanly, the temp file is compared to the original. If larger, it's deleted (auto-skip); if smaller, `os.replace()` moves it into place.

```mermaid
flowchart TD
    Input["input.mp4"]
    Build["build_command(input, tmp_output)
encoder + quality flags
resize filter
HDR metadata passthrough
audio: stream-copy or re-encode
subtitles: mov_text (MP4) or copy (MKV)
container flags: hvc1, faststart (MP4 only)"]
    Ffmpeg["ffmpeg -y ... tmp.mp4
subprocess.Popen(stderr)
real-time progress (%)
cancel_check: kill if tmp > input size"]
    Success{"success?"}
    SizeCheck{"tmp size
> original?"}
    SkipLarge["auto-skip
delete tmp
keep original"]
    ReplaceOut["os.replace
tmp → out"]
    UnlinkFail["unlink tmp.mp4
(clean up trash)"]

    Input --> Build --> Ffmpeg --> Success
    Success -->|yes| SizeCheck
    SizeCheck -->|yes| SkipLarge --> Done["done (skipped, continue)"]
    SizeCheck -->|no| ReplaceOut --> DoneDone["done"]
    Success -->|no| UnlinkFail --> Failed["failed / halt"]
```

### Temp file naming

```
Normal mode:   video.mp4  →  video_h265.h265-tmp.mp4  →  video_h265.mp4
--yolo mode:   video.mp4  →  video.h265-tmp.mp4       →  video.mp4
```

The temp suffix `.h265-tmp` is inserted before the container extension. On success, `os.replace()` atomically renames the temp file to the final output path.

## Auto-skip and halt-on-increase

Auto-skip is always active (no flag needed). `--halt-on-increase` adds a batch-level gate on top.

```mermaid
flowchart TD
    EncodeDone["encode finishes
output size > input size?"]
    AutoSkip["auto-skip: delete tmp
keep original untouched
log warning"]
    HaltCheck{"--halt-on-increase
set?"}
    Continue["continue with
next file"]
    HaltBatch["halt entire batch
print warning"]

    EncodeDone -->|yes| AutoSkip --> HaltCheck
    HaltCheck -->|yes| HaltBatch
    HaltCheck -->|no| Continue
```

During encoding, a mid-stream abort check polls the temp file every second; if it has already exceeded the original size, ffmpeg is killed early to save cycles.

### `--halt-on-increase` (`-H`)

With this flag, a batch-wide stop is triggered on the first oversized output. Without it, encoding continues with the remaining files (each oversized file is still auto-skipped).

## Crash recovery

No state file needed. The filesystem is the source of truth:

```mermaid
flowchart TD
    Exists{"Final output
exists?"}
    Skip["Skip
encode completed"]
    TempCheck["Check for
temp file"]
    TempExists{"exists?"}
    Reencode["ffmpeg -y overwrites it
re-encode"]
    Fresh["Fresh encode
needed
(auto-skip from oversized
output also lands here:
temp deleted, original
untouched)"]

    Exists -->|yes| Skip
    Exists -->|no| TempCheck --> TempExists
    TempExists -->|exists, prev attempt crashed| Reencode
    TempExists -->|absent| Fresh
```

Because the temp file only becomes the final output via `os.replace()` (atomic on all modern filesystems), a partially-written file can never appear at the final path. Power loss, kill -9, kernel panic: no corruption.

## `--replace` mode (separate path)

No encoding happens. Finds existing `*_h265.*` files and swaps them with their originals.

```mermaid
flowchart TD
    Scan["find_replace_pairs
scan paths for *_h265.* files"]
    Found{"original
found?"}
    DeleteOrig["delete original
(trash or permanent)"]
    Rename["rename _h265 file
to original stem + new ext"]
    WarnSkip["warn, skip"]

    Scan --> Found
    Found -->|yes| DeleteOrig --> Rename --> Done["done"]
    Found -->|no| WarnSkip --> Done
```

Example:
```
video_h265.mp4 + video.mkv
  → trash video.mkv
  → rename video_h265.mp4 → video.mp4
```

## Module map

```mermaid
flowchart LR
    Init["__init__.py
CLI, argparse, orchestration"]
    Encoder["encoder.py
ffmpeg command builder,
subprocess runner"]
    Hardware["hardware.py
encoder detection,
preset/quality mappings"]
    Logger["logger.py
persistent file logging
(app events + ffmpeg stderr)"]
    Pipeline["pipeline.py
file discovery, job prep,
encode loop, replace mode
auto-skip logic, halt-on-increase"]
    Probe["probe.py
ffprobe wrapper,
metadata extraction"]

    Init --> Encoder
    Init --> Pipeline
    Init --> Hardware
    Pipeline --> Encoder
    Pipeline --> Probe
    Pipeline --> Logger
    Hardware --> Encoder
```

Each module uses `from __future__ import annotations`, dataclasses for structured data, and `pathlib.Path` exclusively.
