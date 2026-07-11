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
auto-retry crash 3 times
continue on failure
--halt-on-increase stops batch"]
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
    RetryCheck{"< 3 attempts
used?"}

    Input --> Build --> Ffmpeg --> Success
    Success -->|yes| SizeCheck
    SizeCheck -->|yes| SkipLarge --> Done["done (skipped, continue)"]
    SizeCheck -->|no| ReplaceOut --> DoneDone["done"]
    Success -->|no| UnlinkFail --> RetryCheck
    RetryCheck -->|yes| Ffmpeg
    RetryCheck -->|no| Failed["failed, continue"]
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

On ffmpeg crash (non-zero exit), the temp file is cleaned up and the encode is automatically retried up to 2 more times (3 attempts total). If all attempts fail, the pipeline logs the failure and moves to the next file — the batch is not interrupted.

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
    Vmaf["vmaf.py
VMAF-based auto-CRF
(Libvmaf probing,
linear regression fit)"]

    Init --> Encoder
    Init --> Pipeline
    Init --> Hardware
    Init --> Vmaf
    Pipeline --> Encoder
    Pipeline --> Probe
    Pipeline --> Vmaf
    Pipeline --> Logger
    Hardware --> Encoder
```

## VMAF evaluation mode (`--vmaf`)

`--vmaf` is a standalone evaluation mode (mutually exclusive with `--replace`,
`--yolo`, and encoding flags). It probes each file to find the optimal CRF for
a target VMAF score, then reports the results — no encoding is performed.

### VMAF probing flow

```mermaid
flowchart TD
    Start["--vmaf set?"]
    SkipProbe["skip"]
    Extract["_extract_segment
60s from 25% mark (stream copy)
to temp file
avoids titles / credits"]
    ProbeSeg["probe segment
(ffprobe for pix_fmt,
bit depth)"]
    Loop["for each candidate CRF
18, 23, 28, 33:"]
    EncodeSeg["encode segment
(medium preset by default,
respects --preset and --cpu)"]
    EarlyStop{"VMAF < target
and ≥ 2 data points?"}
    Vmaf["_compute_vmaf_score
ffmpeg libvmaf filter
JSON output"]
    Store["store (CRF, VMAF) pair"]
    Refine{"all on one side
of target?"}
    ProbeExtra["probe one more CRF
38 (above) or 13 (below)"]
    Fit["_fit_crf
linear regression
VMAF = a·CRF + b
solve for target"]
    Report["print recommended CRF
per file
no encoding"]

    Start -->|no| SkipProbe
    Start -->|yes| Extract --> ProbeSeg --> Loop
    Loop --> EncodeSeg --> Vmaf --> Store
    Store --> EarlyStop
    EarlyStop -->|no, continue| Loop
    EarlyStop -->|yes, bracketed| Fit
    Loop -->|all 4 done| Refine
    Refine -->|extreme| ProbeExtra --> Fit
    Refine -->|bracketed| Fit
    Fit --> Report
```

### Key design decisions

- **Evaluation only**: no encoding happens after probing. The user gets a
  recommended CRF and decides what to do with it.
- **Same encoder and preset**: probe encodes use the user's chosen `--preset`
  and `--cpu` setting, matching real encode conditions.
- **Parallel probing**: all files probe concurrently using a thread pool.
  Results are printed atomically per-file.
- **Early stop**: probing stops as soon as a VMAF score falls below the
  target, since we have a bracket.

### Design decisions

- **60-second sample from the 25% mark**: avoids studio logos, title
  sequences, and end credits that don't represent the video's typical content.
- **For short videos (< 120s)**: samples from the beginning since there's no
  risk of unrepresentative introductory content.
- **Per-file probing**: each file gets its own CRF. Content complexity varies
  wildly — a CRF that works for animation may overshoot for live-action.
- **Linear regression**: VMAF and CRF have an approximately linear relationship
  in the useful range (CRF 18–35, VMAF ~98–85). Simple least-squares fit
  works better than binary search because VMAF measurements have some noise.
- **Same preset as real encode**: the probe encodes use the user's `--preset`
  (not hardcoded `veryfast`), so VMAF measurements reflect actual quality.

Each module uses `from __future__ import annotations`, dataclasses for structured data, and `pathlib.Path` exclusively.
